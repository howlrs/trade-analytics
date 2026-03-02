import type { PoolInfo, PositionInfo, RebalanceDecision, TransactionResult } from '../types/index.js'
import type { Config, PoolConfig } from '../types/config.js'
import { evaluateRebalanceTrigger, recordRebalanceForDay, transferDailyState } from '../strategy/trigger.js'
import { calculateOptimalRange } from '../strategy/range.js'
import { calculateVolatilityBasedTicks } from '../strategy/volatility.js'
import { closePosition, openPosition, addLiquidity, fetchPositionFees, fetchPositionRewards } from './position.js'
import { estimatePositionAmounts, coinBPriceInCoinA, getCetusUsdPrice, rewardToUsd } from './price.js'
import { calculateSwapPlan, executeSwap } from './swap.js'
import { getLogger } from '../utils/logger.js'
import { recordEvent } from '../utils/event-log.js'
import { getSuiClient } from '../utils/sui.js'
import { feeTracker } from '../utils/fee-tracker.js'
import { saveRebalanceTime, loadRebalanceTimes } from '../utils/state.js'
import type { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'

const DECIMALS_A = 6 // USDC
const DECIMALS_B = 9 // SUI

// Minimum SUI balance required before starting rebalance (0.15 SUI)
// Covers gas for up to 3 transactions: close + swap + open
const MIN_SUI_FOR_GAS = 150_000_000n

// SUI reserved from position funds to keep in wallet for future gas.
// Must be >= MIN_SUI_FOR_GAS so the bot can pass its own preflight check
// after reserving gas. 1.0 SUI ensures wallet balance stays above 1 SUI
// and provides ~100+ rebalances of headroom at ~0.006 SUI/rebalance.
const GAS_RESERVE = 1_000_000_000n

// Minimum idle funds worth deploying (low threshold: gas ~0.005 SUI is cheap)
const MIN_IDLE_DEPLOY_A = 1_000_000n   // $1 USDC (6 decimals)
const MIN_IDLE_DEPLOY_B = 100_000_000n // 0.1 SUI (9 decimals)

const lastRebalanceTimes = new Map<string, number>()

/**
 * Load persisted rebalance times from state.json into the in-memory map.
 * Call once at startup so minTimeInRangeSec guard survives restarts.
 */
export function initRebalanceTimes(): void {
  const log = getLogger()
  const persisted = loadRebalanceTimes()
  const count = Object.keys(persisted).length
  for (const [posId, ts] of Object.entries(persisted)) {
    lastRebalanceTimes.set(posId, ts)
  }
  if (count > 0) {
    log.info('Restored rebalance times from state.json', { count })
  }
}

/** Tracks pre-fit tick width for post-fit guard (prevents immediate narrow→range-out oscillation) */
const preFitTickWidth = new Map<string, { width: number; fittedAt: number }>()
const RANGE_FIT_COOLDOWN_SEC = 21600

export function getLastRebalanceTime(positionId: string): number | undefined {
  return lastRebalanceTimes.get(positionId)
}

/**
 * Query wallet balances for coinA (USDC) and coinB (SUI) after closing a position.
 */
async function getWalletBalances(
  owner: string,
  coinTypeA: string,
  coinTypeB: string,
): Promise<{ balanceA: bigint; balanceB: bigint }> {
  const client = getSuiClient()

  const [balA, balB] = await Promise.all([
    client.getBalance({ owner, coinType: coinTypeA }),
    client.getBalance({ owner, coinType: coinTypeB }),
  ])

  return {
    balanceA: BigInt(balA.totalBalance),
    balanceB: BigInt(balB.totalBalance),
  }
}

/**
 * Deploy idle wallet funds into an existing position after rebalance.
 * Loops internally until idle funds converge below threshold (max 5 iterations).
 * Each iteration: query balance → swap (capped) → addLiquidity.
 * Best-effort: failures are logged but do not affect the rebalance result.
 *
 * IMPORTANT: This function is ONLY called post-rebalance, never periodically.
 * Harvest proceeds remain in the wallet untouched.
 */
export async function deployIdleFunds(
  pool: PoolInfo,
  positionId: string,
  tickLower: number,
  tickUpper: number,
  owner: string,
  config: Config,
  keypair: Ed25519Keypair,
): Promise<void> {
  const log = getLogger()
  const MAX_ITERATIONS = 5

  log.info('Waiting for RPC to settle before idle fund deployment...')
  await new Promise(r => setTimeout(r, 5000))

  for (let iteration = 1; iteration <= MAX_ITERATIONS; iteration++) {
    const wallet = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)
    const idleA = wallet.balanceA
    const idleB = wallet.balanceB > GAS_RESERVE ? wallet.balanceB - GAS_RESERVE : 0n

    // Exit if both balances below threshold
    if (idleA < MIN_IDLE_DEPLOY_A && idleB < MIN_IDLE_DEPLOY_B) {
      log.info('Idle fund deployment: converged below threshold', {
        iteration,
        idleA_USDC: (Number(idleA) / 1e6).toFixed(4),
        idleB_SUI: (Number(idleB) / 1e9).toFixed(4),
      })
      if (iteration === 1) {
        recordEvent('idle_deploy_skip', {
          reason: 'below_threshold',
          idleA_USDC: (Number(idleA) / 1e6).toFixed(4),
          idleB_SUI: (Number(idleB) / 1e9).toFixed(4),
        }, positionId, pool.poolId)
      }
      return
    }

    log.info('Deploying idle wallet funds into position', {
      positionId,
      iteration,
      maxIterations: MAX_ITERATIONS,
      idleA_USDC: (Number(idleA) / 1e6).toFixed(4),
      idleB_SUI: (Number(idleB) / 1e9).toFixed(4),
    })

    // Calculate if a swap is needed to match the position's ratio
    const swapPlan = calculateSwapPlan(
      pool,
      tickLower,
      tickUpper,
      idleA,
      idleB,
      DECIMALS_A,
      DECIMALS_B,
    )

    let deployA = idleA
    let deployB = idleB

    if (swapPlan.needSwap) {
      const sourceBalance = swapPlan.a2b ? idleA : idleB
      const swapRatio = sourceBalance > 0n
        ? Number(swapPlan.swapAmount) / Number(sourceBalance)
        : Infinity

      const maxRatio = config.maxIdleSwapRatio
      let actualSwapAmount = swapPlan.swapAmount
      let capped = false

      if (maxRatio > 0 && swapRatio > maxRatio) {
        actualSwapAmount = BigInt(Math.floor(Number(sourceBalance) * maxRatio))
        capped = true
        log.info('Idle deploy: swap ratio capped', {
          iteration,
          originalSwapRatio: (swapRatio * 100).toFixed(1) + '%',
          maxRatio: (maxRatio * 100).toFixed(1) + '%',
          originalAmount: swapPlan.swapAmount.toString(),
          cappedAmount: actualSwapAmount.toString(),
          a2b: swapPlan.a2b,
        })
      }

      log.info('Idle deploy: executing swap for optimal ratio', {
        iteration,
        a2b: swapPlan.a2b,
        amount: actualSwapAmount.toString(),
        capped,
      })

      const swapResult = await executeSwap(
        pool,
        swapPlan.a2b,
        actualSwapAmount,
        config.slippageTolerance,
        keypair,
        false,
        config.maxSwapCostPct,
      )

      if (!swapResult.success) {
        log.warn('Idle deploy: swap failed, attempting addLiquidity with current balances', {
          iteration,
          error: swapResult.error,
        })
      } else {
        // Re-query after swap — retry until balance reflects swap output
        const preSwapA = idleA
        const preSwapB = idleB
        const swapWasA2B = swapPlan.a2b
        let settled = false
        for (let attempt = 1; attempt <= 4; attempt++) {
          await new Promise(r => setTimeout(r, attempt <= 2 ? 3000 : 5000))
          const postSwap = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)
          deployA = postSwap.balanceA
          deployB = postSwap.balanceB > GAS_RESERVE ? postSwap.balanceB - GAS_RESERVE : 0n

          const receivedA = !swapWasA2B && deployA > preSwapA
          const receivedB = swapWasA2B && deployB > preSwapB
          if (receivedA || receivedB) {
            log.info('Idle deploy: post-swap balance settled', {
              iteration,
              attempt,
              deployA_USDC: (Number(deployA) / 1e6).toFixed(4),
              deployB_SUI: (Number(deployB) / 1e9).toFixed(4),
            })
            settled = true
            break
          }
          log.info('Idle deploy: waiting for post-swap balance to settle...', {
            iteration,
            attempt,
            deployA_USDC: (Number(deployA) / 1e6).toFixed(4),
            deployB_SUI: (Number(deployB) / 1e9).toFixed(4),
          })
        }
        if (!settled) {
          log.warn('Idle deploy: post-swap balance did not settle, proceeding with current balance')
        }
      }
    }

    let result = await addLiquidity(
      pool.poolId,
      positionId,
      pool.coinTypeA,
      pool.coinTypeB,
      tickLower,
      tickUpper,
      deployA.toString(),
      deployB.toString(),
      config.slippageTolerance,
      false,
      keypair,
      false,
      pool.rewarderCoinTypes,
      pool.currentSqrtPrice,
    )

    // Retry once if addLiquidity failed with 'Insufficient balance' (stale RPC)
    if (!result.success && result.error?.includes('Insufficient balance')) {
      log.info('Idle deploy: retrying after re-querying balances (stale RPC suspected)', { iteration })
      await new Promise(r => setTimeout(r, 3000))

      const retryWallet = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)
      deployA = retryWallet.balanceA
      deployB = retryWallet.balanceB > GAS_RESERVE ? retryWallet.balanceB - GAS_RESERVE : 0n

      result = await addLiquidity(
        pool.poolId,
        positionId,
        pool.coinTypeA,
        pool.coinTypeB,
        tickLower,
        tickUpper,
        deployA.toString(),
        deployB.toString(),
        config.slippageTolerance,
        false,
        keypair,
        false,
        pool.rewarderCoinTypes,
        pool.currentSqrtPrice,
      )
    }

    if (result.success) {
      log.info('Idle deploy iteration succeeded', {
        iteration,
        digest: result.digest,
        deployA_USDC: (Number(deployA) / 1e6).toFixed(4),
        deployB_SUI: (Number(deployB) / 1e9).toFixed(4),
      })
      recordEvent('idle_deploy_complete', {
        iteration,
        digest: result.digest,
        gasCost: result.gasCost.toString(),
        deployA_USDC: (Number(deployA) / 1e6).toFixed(4),
        deployB_SUI: (Number(deployB) / 1e9).toFixed(4),
      }, positionId, pool.poolId)

      // Wait briefly before next iteration's balance query
      if (iteration < MAX_ITERATIONS) {
        await new Promise(r => setTimeout(r, 3000))
      }
      // Continue loop — check if more idle funds remain
    } else {
      log.warn('Idle fund deployment: addLiquidity failed, stopping iterations', {
        iteration,
        error: result.error,
      })
      recordEvent('idle_deploy_error', {
        iteration,
        error: result.error,
        deployA_USDC: (Number(deployA) / 1e6).toFixed(4),
        deployB_SUI: (Number(deployB) / 1e9).toFixed(4),
      }, positionId, pool.poolId)
      return // Stop — don't retry addLiquidity failures in loop
    }
  }

  log.info('Idle fund deployment: max iterations reached', { maxIterations: MAX_ITERATIONS })
}

/** Fee context passed from the scheduler's per-cycle fee fetch */
export interface FeeContext {
  observedHourlyFeeUsd?: number
  positionValueUsd?: number
}

/** Guardrail params passed from the scheduler */
export interface GuardrailContext {
  waitAfterRangeoutSec?: number
  maxRebalancesPerDay?: number
  minTimeInRangeSec?: number
  positionOpenedAt?: number
  optimalTickWidth?: number
  volStabilityCount?: number
}

export async function checkAndRebalance(
  pool: PoolInfo,
  position: PositionInfo,
  config: Config,
  poolConfig: PoolConfig,
  keypair: Ed25519Keypair,
  feeContext?: FeeContext,
  guardrailContext?: GuardrailContext,
): Promise<{ decision: RebalanceDecision; result: TransactionResult | null; newPositionId?: string }> {
  const log = getLogger()
  const owner = keypair.getPublicKey().toSuiAddress()

  const decision = evaluateRebalanceTrigger(pool, position, {
    rebalanceThreshold: config.rebalanceThreshold,
    decimalsA: DECIMALS_A,
    decimalsB: DECIMALS_B,
    lastRebalanceTime: getLastRebalanceTime(position.positionId),
    poolFeeRate: pool.feeRate / 1_000_000, // Convert from Cetus basis (e.g. 2500 → 0.0025)
    observedHourlyFeeUsd: feeContext?.observedHourlyFeeUsd,
    positionValueUsd: feeContext?.positionValueUsd,
    waitAfterRangeoutSec: guardrailContext?.waitAfterRangeoutSec,
    maxRebalancesPerDay: guardrailContext?.maxRebalancesPerDay,
    minTimeInRangeSec: guardrailContext?.minTimeInRangeSec,
    positionOpenedAt: guardrailContext?.positionOpenedAt,
    optimalTickWidth: guardrailContext?.optimalTickWidth,
    volStabilityCount: guardrailContext?.volStabilityCount,
    fallbackDailyVolumeRatio: config.fallbackDailyVolumeRatio,
    maxBreakevenHours: config.maxBreakevenHours,
  })

  log.info('Rebalance evaluation', {
    positionId: position.positionId,
    shouldRebalance: decision.shouldRebalance,
    trigger: decision.trigger,
    reason: decision.reason,
  })

  recordEvent('rebalance_check', {
    shouldRebalance: decision.shouldRebalance,
    trigger: decision.trigger,
    reason: decision.reason,
    currentPrice: decision.currentPrice,
    currentLower: decision.currentLower,
    currentUpper: decision.currentUpper,
  }, position.positionId, pool.poolId)

  if (!decision.shouldRebalance) {
    return { decision, result: null }
  }

  // Pre-flight: ensure wallet has enough SUI for gas
  const { balanceB: walletSui } = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)
  if (walletSui < MIN_SUI_FOR_GAS) {
    const msg = `Insufficient SUI for gas: ${(Number(walletSui) / 1e9).toFixed(4)} SUI < ${(Number(MIN_SUI_FOR_GAS) / 1e9).toFixed(4)} SUI minimum`
    log.error(msg)
    recordEvent('rebalance_error', { step: 'preflight', error: msg }, position.positionId, pool.poolId)
    return {
      decision,
      result: { success: false, digest: null, gasCost: 0n, error: msg },
    }
  }

  // Calculate claimable fees+rewards USD for free-rebalance check
  let claimableUsd: number | undefined
  try {
    const fees = await fetchPositionFees([position.positionId])
    const positionFees = fees.get(position.positionId)
    const feeA = positionFees?.feeA ?? 0n
    const feeB = positionFees?.feeB ?? 0n
    const rewardAmounts = await fetchPositionRewards(pool.poolId, position.positionId)

    const suiPriceUsdc = coinBPriceInCoinA(pool, DECIMALS_A, DECIMALS_B)
    const cetusUsdPrice = await getCetusUsdPrice(suiPriceUsdc)

    const feeAUsd = Number(feeA) / (10 ** DECIMALS_A)
    const feeBUsd = (Number(feeB) / (10 ** DECIMALS_B)) * suiPriceUsdc
    let rewardsUsd = 0
    for (const r of rewardAmounts) {
      rewardsUsd += rewardToUsd(r.coinType, r.amount, suiPriceUsdc, cetusUsdPrice)
    }
    claimableUsd = feeAUsd + feeBUsd + rewardsUsd
    log.info('Pre-rebalance claimable fees+rewards', {
      claimableUsd: claimableUsd.toFixed(4),
      feeAUsd: feeAUsd.toFixed(4),
      feeBUsd: feeBUsd.toFixed(4),
      rewardsUsd: rewardsUsd.toFixed(4),
      freeHarvestThreshold: config.rebalanceFreeHarvestUsd,
    })
  } catch (err) {
    log.warn('Failed to fetch claimable fees for free-rebalance check', {
      error: (err as Error).message,
    })
  }

  // Calculate volatility-based tick width for dynamic strategy
  let volatilityTickWidth: number | undefined
  if (poolConfig.strategy === 'dynamic') {
    const volResult = await calculateVolatilityBasedTicks(
      pool.poolId,
      pool.tickSpacing,
      poolConfig.volLookbackHours,
      poolConfig.volTickWidthMin,
      poolConfig.volTickWidthMax,
    )
    if (volResult) {
      volatilityTickWidth = volResult.tickWidth
      log.info('Volatility-based tick width selected', {
        sigma: volResult.sigma.toFixed(2),
        tickWidth: volResult.tickWidth,
      })
    } else {
      log.info('Volatility engine returned null, using pct-based fallback')
    }
  }

  // Record pre-fit tick width if this is a range-fit trigger
  if (decision.trigger === 'range-fit') {
    const currentWidth = position.tickUpperIndex - position.tickLowerIndex
    preFitTickWidth.set(position.positionId, { width: currentWidth, fittedAt: Date.now() })
  }

  // Post-fit guard: if range-out after recent fit, ensure minimum width
  if (decision.trigger === 'range-out') {
    const preFit = preFitTickWidth.get(position.positionId)
    if (preFit && (Date.now() - preFit.fittedAt) / 1000 < RANGE_FIT_COOLDOWN_SEC) {
      if (volatilityTickWidth != null && volatilityTickWidth < preFit.width) {
        log.info('Post-fit guard: ensuring minimum width from pre-fit state', {
          preFitWidth: preFit.width,
          computedWidth: volatilityTickWidth,
        })
        volatilityTickWidth = preFit.width
      }
    }
  }

  // Calculate new optimal range
  const newRange = calculateOptimalRange(pool, poolConfig, DECIMALS_A, DECIMALS_B, volatilityTickWidth)
  decision.newLower = newRange.priceLower
  decision.newUpper = newRange.priceUpper

  recordEvent('rebalance_triggered', {
    trigger: decision.trigger,
    newLower: newRange.priceLower,
    newUpper: newRange.priceUpper,
    tickLower: newRange.tickLower,
    tickUpper: newRange.tickUpper,
  }, position.positionId, pool.poolId)

  // Snapshot wallet balances BEFORE removing liquidity to isolate position funds
  const preClose = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)

  // --- Step 1: Close existing position (remove liquidity) ---
  let closeGas = 0n
  if (position.liquidity > 0n) {
    log.info('Step 1/3: Removing liquidity', { positionId: position.positionId, liquidity: position.liquidity.toString() })
    const closeResult = await closePosition(
      pool.poolId,
      position.positionId,
      pool.coinTypeA,
      pool.coinTypeB,
      position.liquidity,
      pool.currentSqrtPrice,
      position.tickLowerIndex,
      position.tickUpperIndex,
      config.slippageTolerance,
      keypair,
      config.dryRun,
      pool.rewarderCoinTypes,
    )

    if (!closeResult.success) {
      log.error('Failed to close position', { error: closeResult.error })
      recordEvent('rebalance_error', { step: 'close', error: closeResult.error }, position.positionId, pool.poolId)
      return { decision, result: closeResult }
    }

    closeGas = closeResult.gasCost
    recordEvent('rebalance_close', {
      digest: closeResult.digest,
      gasCost: closeResult.gasCost.toString(),
    }, position.positionId, pool.poolId)
  } else {
    log.info('Step 1/3: Position already empty, skipping remove')
  }

  // --- Step 2: Swap to optimal ratio ---
  log.info('Step 2/3: Calculating optimal swap')

  let balanceA: bigint
  let balanceB: bigint

  if (position.liquidity === 0n) {
    // Recovery mode: position was already closed (e.g. interrupted rebalance).
    // Use wallet funds directly — no delta isolation needed.
    const wallet = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)
    balanceA = wallet.balanceA
    balanceB = wallet.balanceB
    log.info('Recovery mode: using wallet funds for 0-liquidity position', {
      balanceA_USDC: (Number(balanceA) / 1e6).toFixed(4),
      balanceB_SUI: (Number(balanceB) / 1e9).toFixed(4),
    })

    // Minimum balance guard: abort if no meaningful funds to recover
    const MIN_RECOVERY_USD_VALUE = 1_000_000n  // $1 USDC (6 decimals)
    const availableBForCheck = balanceB > GAS_RESERVE ? balanceB - GAS_RESERVE : 0n
    if (balanceA < MIN_RECOVERY_USD_VALUE && availableBForCheck === 0n) {
      const msg = 'Insufficient funds for recovery: balanceA < $1 USDC and no usable SUI after gas reserve'
      log.warn(msg, {
        balanceA_USDC: (Number(balanceA) / 1e6).toFixed(4),
        availableB_SUI: (Number(availableBForCheck) / 1e9).toFixed(4),
      })
      recordEvent('rebalance_error', { step: 'recovery_guard', error: msg }, position.positionId, pool.poolId)
      return {
        decision,
        result: { success: false, digest: null, gasCost: 0n, error: msg },
      }
    }
  } else if (config.dryRun) {
    // Dry-run: estimate from position liquidity (no actual close happened)
    const estimated = estimatePositionAmounts(pool, position)
    balanceA = estimated.amountA
    balanceB = estimated.amountB
    log.info('Dry-run: using estimated position amounts', {
      amountA_USDC: (Number(balanceA) / 1e6).toFixed(4),
      amountB_SUI: (Number(balanceB) / 1e9).toFixed(4),
    })
  } else {
    // Normal close: poll for RPC to reflect on-chain state
    const POLL_INTERVALS = [5000, 5000, 10000] // 5s, 5s, 10s (total 20s max)
    let positionFundsA = 0n
    let positionFundsB = 0n

    for (let i = 0; i < POLL_INTERVALS.length; i++) {
      log.info(`Waiting for RPC state to settle after close (attempt ${i + 1}/${POLL_INTERVALS.length})...`)
      await new Promise(r => setTimeout(r, POLL_INTERVALS[i]))

      const postClose = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)
      positionFundsA = postClose.balanceA - preClose.balanceA
      positionFundsB = postClose.balanceB - preClose.balanceB

      if (positionFundsA > 0n || positionFundsB > 0n) {
        log.info('RPC state settled, funds detected', {
          attempt: i + 1,
          deltaA: positionFundsA.toString(),
          deltaB: positionFundsB.toString(),
        })
        break
      }
    }

    if (positionFundsA > 0n || positionFundsB > 0n) {
      balanceA = positionFundsA > 0n ? positionFundsA : 0n
      balanceB = positionFundsB > 0n ? positionFundsB : 0n
    } else {
      const msg = 'Position close returned no funds after 3 attempts (delta <= 0). Aborting rebalance for safety.'
      log.error(msg, {
        deltaA: positionFundsA.toString(),
        deltaB: positionFundsB.toString(),
      })
      recordEvent('rebalance_error', { step: 'fund_isolation', error: msg }, position.positionId, pool.poolId)
      return {
        decision,
        result: { success: false, digest: null, gasCost: closeGas, error: msg },
      }
    }
  }

  const availableB = balanceB > GAS_RESERVE ? balanceB - GAS_RESERVE : 0n

  log.info('Funds for new position', {
    balanceA_USDC: (Number(balanceA) / 1e6).toFixed(4),
    balanceB_SUI: (Number(balanceB) / 1e9).toFixed(4),
    availableB_SUI: (Number(availableB) / 1e9).toFixed(4),
    gasReserve_SUI: (Number(GAS_RESERVE) / 1e9).toFixed(4),
  })

  let totalGas = closeGas
  let swapFree = false

  // Re-query balances after swap, isolate position funds
  let finalBalanceA: bigint
  let finalBalanceB: bigint

  if (config.swapFreeRebalance) {
    // --- Swap-free mode: skip full swap, but allow small ratio-correction swap ---
    swapFree = true

    // Check if a small swap is needed and permitted to fix ratio mismatch
    const ratioSwapPlan = calculateSwapPlan(
      pool,
      newRange.tickLower,
      newRange.tickUpper,
      balanceA,
      availableB,
      DECIMALS_A,
      DECIMALS_B,
    )

    // Use maxIdleSwapRatio as the cap — same limit used for idle deployment.
    // Range-out triggers allow up to 50% to handle bigger balance skew.
    const maxRatio = decision.trigger === 'range-out'
      ? Math.max(config.maxIdleSwapRatio, 0.50)
      : config.maxIdleSwapRatio
    let didRatioSwap = false

    if (ratioSwapPlan.needSwap && maxRatio > 0) {
      // Calculate swap ratio relative to the source token's balance
      const sourceBalance = ratioSwapPlan.a2b ? balanceA : availableB
      const swapRatio = sourceBalance > 0n
        ? Number(ratioSwapPlan.swapAmount) / Number(sourceBalance)
        : Infinity

      // Cap swap amount if it exceeds maxRatio (partial swap, not skip)
      let actualSwapAmount = ratioSwapPlan.swapAmount
      let capped = false
      if (swapRatio > maxRatio) {
        actualSwapAmount = BigInt(Math.floor(Number(sourceBalance) * maxRatio))
        capped = true
        log.info('Step 2/3: Swap-free mode — capping ratio-correction swap', {
          originalSwapRatio: (swapRatio * 100).toFixed(1) + '%',
          maxRatio: (maxRatio * 100).toFixed(1) + '%',
          originalAmount: ratioSwapPlan.swapAmount.toString(),
          cappedAmount: actualSwapAmount.toString(),
          a2b: ratioSwapPlan.a2b,
          reason: ratioSwapPlan.reason,
        })
      } else {
        log.info('Step 2/3: Swap-free mode — executing ratio-correction swap', {
          a2b: ratioSwapPlan.a2b,
          swapAmount: actualSwapAmount.toString(),
          swapRatio: (swapRatio * 100).toFixed(1) + '%',
          maxRatio: (maxRatio * 100).toFixed(1) + '%',
          reason: ratioSwapPlan.reason,
        })
      }

      const swapResult = await executeSwap(
        pool,
        ratioSwapPlan.a2b,
        actualSwapAmount,
        config.slippageTolerance,
        keypair,
        config.dryRun,
        config.maxSwapCostPct,
      )

      if (swapResult.success) {
        totalGas += swapResult.gasCost
        didRatioSwap = true
        log.info('Ratio-correction swap completed', {
          digest: swapResult.digest,
          capped,
          swappedAmount: actualSwapAmount.toString(),
        })
        recordEvent('rebalance_ratio_swap', {
          a2b: ratioSwapPlan.a2b,
          amount: actualSwapAmount.toString(),
          swapRatio: swapRatio.toFixed(4),
          capped,
          digest: swapResult.digest,
          gasCost: swapResult.gasCost.toString(),
        }, position.positionId, pool.poolId)
      } else {
        log.warn('Ratio-correction swap failed, proceeding without swap', {
          error: swapResult.error,
        })
      }
    } else {
      log.info('Step 2/3: Swap-free mode — no swap needed or ratio swap disabled', {
        needSwap: ratioSwapPlan.needSwap,
        maxRatio,
        balanceA_USDC: (Number(balanceA) / 1e6).toFixed(4),
        availableB_SUI: (Number(availableB) / 1e9).toFixed(4),
      })
    }

    // Wait for RPC to reflect on-chain state
    if (!config.dryRun && (didRatioSwap || position.liquidity > 0n)) {
      log.info('Waiting for RPC state to settle...')
      await new Promise(r => setTimeout(r, 3000))
    }

    if (didRatioSwap && !config.dryRun) {
      // Re-query balances after ratio swap, using delta isolation.
      // If RPC hasn't settled yet, deployIdleFunds will pick up the remainder.
      const postSwap = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)

      if (position.liquidity === 0n) {
        finalBalanceA = postSwap.balanceA
        const rawFinalB = postSwap.balanceB
        finalBalanceB = rawFinalB > GAS_RESERVE ? rawFinalB - GAS_RESERVE : 0n
      } else {
        const deltaA = postSwap.balanceA - preClose.balanceA
        const deltaB = postSwap.balanceB - preClose.balanceB
        finalBalanceA = deltaA > 0n ? deltaA : 0n
        const rawFinalB = deltaB > 0n ? deltaB : 0n
        finalBalanceB = rawFinalB > GAS_RESERVE ? rawFinalB - GAS_RESERVE : 0n
      }
    } else if (config.dryRun && didRatioSwap) {
      // Dry-run with ratio swap: use swap plan targets
      finalBalanceA = ratioSwapPlan.targetAmountA
      finalBalanceB = ratioSwapPlan.targetAmountB > GAS_RESERVE ? ratioSwapPlan.targetAmountB - GAS_RESERVE : 0n
    } else {
      // No swap executed: use current balances
      finalBalanceA = balanceA
      finalBalanceB = availableB
    }
  } else {
    // --- Standard mode: calculate and execute swap ---
    const swapPlan = calculateSwapPlan(
      pool,
      newRange.tickLower,
      newRange.tickUpper,
      balanceA,
      availableB,
      DECIMALS_A,
      DECIMALS_B,
    )

    if (swapPlan.needSwap) {
      log.info('Step 2/3: Executing swap', { reason: swapPlan.reason, a2b: swapPlan.a2b, amount: swapPlan.swapAmount.toString() })

      const swapResult = await executeSwap(
        pool,
        swapPlan.a2b,
        swapPlan.swapAmount,
        config.slippageTolerance,
        keypair,
        config.dryRun,
        config.maxSwapCostPct,
      )

      if (!swapResult.success) {
        log.error('CRITICAL: Position closed but swap failed! Funds in wallet.', {
          error: swapResult.error,
        })
        recordEvent('rebalance_error', { step: 'swap', error: swapResult.error }, position.positionId, pool.poolId)
        return { decision, result: swapResult }
      }

      totalGas += swapResult.gasCost
      log.info('Swap completed', { digest: swapResult.digest })
      recordEvent('rebalance_swap', {
        a2b: swapPlan.a2b,
        amount: swapPlan.swapAmount.toString(),
        digest: swapResult.digest,
        gasCost: swapResult.gasCost.toString(),
      }, position.positionId, pool.poolId)
    } else {
      log.info('Step 2/3: No swap needed', { reason: swapPlan.reason })
    }

    // Wait for RPC to reflect on-chain state after swap/close
    if (!config.dryRun && (swapPlan.needSwap || position.liquidity > 0n)) {
      log.info('Waiting for RPC state to settle...')
      await new Promise(r => setTimeout(r, 3000))
    }

    if (config.dryRun) {
      // Dry-run: reuse swap plan targets (no actual TX happened)
      finalBalanceA = swapPlan.targetAmountA
      finalBalanceB = swapPlan.targetAmountB > GAS_RESERVE ? swapPlan.targetAmountB - GAS_RESERVE : 0n
    } else {
      const postSwap = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)

      if (position.liquidity === 0n) {
        // Recovery mode: use wallet funds directly (no delta isolation)
        finalBalanceA = postSwap.balanceA
        const rawFinalB = postSwap.balanceB
        finalBalanceB = rawFinalB > GAS_RESERVE ? rawFinalB - GAS_RESERVE : 0n
      } else {
        const deltaA = postSwap.balanceA - preClose.balanceA
        const deltaB = postSwap.balanceB - preClose.balanceB

        // Use only position-derived funds; if delta is negative (gas consumed more), use 0
        finalBalanceA = deltaA > 0n ? deltaA : 0n
        const rawFinalB = deltaB > 0n ? deltaB : 0n
        finalBalanceB = rawFinalB > GAS_RESERVE ? rawFinalB - GAS_RESERVE : 0n
      }
    }
  }

  // --- Step 3: Open new position ---
  log.info('Step 3/3: Opening new position', {
    tickLower: newRange.tickLower,
    tickUpper: newRange.tickUpper,
  })

  log.info('Pre-open balances', {
    amountA_USDC: (Number(finalBalanceA) / 1e6).toFixed(4),
    amountB_SUI: (Number(finalBalanceB) / 1e9).toFixed(4),
  })

  let finalOpenResult = await openPosition(
    pool.poolId,
    pool.coinTypeA,
    pool.coinTypeB,
    newRange.tickLower,
    newRange.tickUpper,
    finalBalanceA.toString(),
    finalBalanceB.toString(),
    config.slippageTolerance,
    keypair,
    config.dryRun,
    pool.rewarderCoinTypes,
    pool.currentSqrtPrice,
  )

  if (!finalOpenResult.success) {
    // --- Swap fallback for range-out trigger in swap-free mode ---
    if (swapFree && decision.trigger === 'range-out') {
      log.warn('Swap-free open failed on range-out, falling back to swap', {
        error: finalOpenResult.error,
      })
      recordEvent('rebalance_swap_fallback', { reason: finalOpenResult.error }, position.positionId, pool.poolId)

      const swapPlan = calculateSwapPlan(
        pool,
        newRange.tickLower,
        newRange.tickUpper,
        finalBalanceA,
        finalBalanceB,
        DECIMALS_A,
        DECIMALS_B,
      )

      if (swapPlan.needSwap) {
        const swapResult = await executeSwap(
          pool,
          swapPlan.a2b,
          swapPlan.swapAmount,
          config.slippageTolerance,
          keypair,
          config.dryRun,
          config.maxSwapCostPct,
        )

        if (!swapResult.success) {
          log.error('CRITICAL: Swap fallback also failed after swap-free open failure!', {
            error: swapResult.error,
          })
          recordEvent('rebalance_error', { step: 'swap_fallback', error: swapResult.error }, position.positionId, pool.poolId)
          return { decision, result: swapResult }
        }

        totalGas += swapResult.gasCost
        recordEvent('rebalance_swap', {
          a2b: swapPlan.a2b,
          amount: swapPlan.swapAmount.toString(),
          digest: swapResult.digest,
          gasCost: swapResult.gasCost.toString(),
          fallback: true,
        }, position.positionId, pool.poolId)

        // Wait for RPC to reflect swap
        if (!config.dryRun) {
          await new Promise(r => setTimeout(r, 3000))
        }

        // Re-query balances after swap
        if (config.dryRun) {
          finalBalanceA = swapPlan.targetAmountA
          finalBalanceB = swapPlan.targetAmountB > GAS_RESERVE ? swapPlan.targetAmountB - GAS_RESERVE : 0n
        } else {
          const postSwap = await getWalletBalances(owner, pool.coinTypeA, pool.coinTypeB)
          if (position.liquidity === 0n) {
            finalBalanceA = postSwap.balanceA
            const rawB = postSwap.balanceB
            finalBalanceB = rawB > GAS_RESERVE ? rawB - GAS_RESERVE : 0n
          } else {
            const deltaA = postSwap.balanceA - preClose.balanceA
            const deltaB = postSwap.balanceB - preClose.balanceB
            finalBalanceA = deltaA > 0n ? deltaA : 0n
            const rawB = deltaB > 0n ? deltaB : 0n
            finalBalanceB = rawB > GAS_RESERVE ? rawB - GAS_RESERVE : 0n
          }
        }
      }

      // Retry openPosition with (potentially swapped) balances
      log.info('Retrying openPosition after swap fallback', {
        amountA_USDC: (Number(finalBalanceA) / 1e6).toFixed(4),
        amountB_SUI: (Number(finalBalanceB) / 1e9).toFixed(4),
      })

      const retryResult = await openPosition(
        pool.poolId,
        pool.coinTypeA,
        pool.coinTypeB,
        newRange.tickLower,
        newRange.tickUpper,
        finalBalanceA.toString(),
        finalBalanceB.toString(),
        config.slippageTolerance,
        keypair,
        config.dryRun,
        pool.rewarderCoinTypes,
        pool.currentSqrtPrice,
      )

      if (!retryResult.success) {
        log.error('CRITICAL: Swap fallback open also failed!', {
          poolId: pool.poolId,
          error: retryResult.error,
        })
        recordEvent('rebalance_error', { step: 'open_after_fallback', error: retryResult.error }, position.positionId, pool.poolId)
        return { decision, result: retryResult }
      }

      // Success via fallback
      swapFree = false
      finalOpenResult = retryResult
    } else {
      log.error('CRITICAL: Position closed + swapped but failed to open new one!', {
        poolId: pool.poolId,
        error: finalOpenResult.error,
      })
      recordEvent('rebalance_error', { step: 'open', error: finalOpenResult.error }, position.positionId, pool.poolId)
      return { decision, result: finalOpenResult }
    }
  }

  totalGas += finalOpenResult.gasCost

  // Detect newly created position ID
  let newPositionId: string | undefined
  if (!config.dryRun) {
    await new Promise(r => setTimeout(r, 2000))
    const allPositions = await import('./position.js').then(m => m.getPositions(owner, [pool.poolId]))
    const newPos = allPositions.find(p =>
      p.positionId !== position.positionId &&
      p.tickLowerIndex === newRange.tickLower &&
      p.tickUpperIndex === newRange.tickUpper &&
      p.liquidity > 0n
    )
    if (newPos) {
      newPositionId = newPos.positionId
      log.info('New position detected', { newPositionId })
    }
  }

  const rebalanceTime = Date.now()
  const rebalancedId = newPositionId ?? position.positionId
  lastRebalanceTimes.set(rebalancedId, rebalanceTime)
  saveRebalanceTime(rebalancedId, rebalanceTime)

  // Transfer daily state BEFORE recording (so the new ID inherits the old count)
  if (newPositionId) {
    transferDailyState(position.positionId, newPositionId)
  }
  // Record rebalance for daily limit tracking
  const effectiveId = newPositionId ?? position.positionId
  recordRebalanceForDay(effectiveId, claimableUsd, config.rebalanceFreeHarvestUsd)
  if (newPositionId) {
    // Transfer pre-fit state to new position
    const preFit = preFitTickWidth.get(position.positionId)
    if (preFit) {
      preFitTickWidth.set(newPositionId, preFit)
      preFitTickWidth.delete(position.positionId)
    }
  }

  // Reset fee tracker: old position is closed, new one starts fresh
  feeTracker.handleRebalance(position.positionId, newPositionId)

  // --- Post-rebalance: deploy idle wallet funds into new position ---
  if (!config.dryRun && newPositionId) {
    try {
      await deployIdleFunds(
        pool, newPositionId, newRange.tickLower, newRange.tickUpper,
        owner, config, keypair,
      )
    } catch (err) {
      // Best-effort: failure here does NOT affect the rebalance result
      log.warn('Idle fund deployment failed (non-critical)', { error: (err as Error).message })
      recordEvent('idle_deploy_error', {
        error: (err as Error).message,
        step: 'post_rebalance',
      }, newPositionId, pool.poolId)
    }
  }

  recordEvent('rebalance_complete', {
    newTickLower: newRange.tickLower,
    newTickUpper: newRange.tickUpper,
    swapFree,
    totalGas: totalGas.toString(),
    digest: finalOpenResult.digest,
    newPositionId,
    amountA_USDC: (Number(finalBalanceA) / 1e6).toFixed(4),
    amountB_SUI: (Number(finalBalanceB) / 1e9).toFixed(4),
  }, position.positionId, pool.poolId)

  log.info('Rebalance completed', {
    positionId: position.positionId,
    newPositionId,
    newTickLower: newRange.tickLower,
    newTickUpper: newRange.tickUpper,
    swapFree,
    totalGas: totalGas.toString(),
  })

  return {
    decision,
    result: {
      success: true,
      digest: finalOpenResult.digest,
      gasCost: totalGas,
      error: null,
    },
    newPositionId,
  }
}
