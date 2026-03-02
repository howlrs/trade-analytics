export type RangeStrategy = 'narrow' | 'wide' | 'dynamic'

export type TriggerType = 'range-out' | 'threshold' | 'time-based' | 'range-fit'

export interface PoolInfo {
  poolId: string
  coinTypeA: string
  coinTypeB: string
  currentSqrtPrice: bigint
  currentTickIndex: number
  feeRate: number
  liquidity: bigint
  tickSpacing: number
  rewarderCoinTypes: string[]
}

export interface PositionInfo {
  positionId: string
  poolId: string
  owner: string
  tickLowerIndex: number
  tickUpperIndex: number
  liquidity: bigint
  feeOwedA: bigint
  feeOwedB: bigint
  rewardAmountOwed: bigint[]
}

export interface RebalanceDecision {
  shouldRebalance: boolean
  trigger: TriggerType | null
  currentPrice: number
  currentLower: number
  currentUpper: number
  newLower: number | null
  newUpper: number | null
  reason: string
}

export interface RewardAmount {
  coinType: string
  amount: bigint
}

export interface HarvestDecision {
  shouldHarvest: boolean
  feeValueA: bigint
  feeValueB: bigint
  rewardAmounts: RewardAmount[]
  totalUsd: number
  estimatedGasCost: bigint
  reason: string
}

export interface TransactionResult {
  success: boolean
  digest: string | null
  gasCost: bigint
  error: string | null
}
