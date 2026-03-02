import { z } from 'zod'

export const PoolConfigSchema = z.object({
  poolId: z.string().min(1),
  positionIds: z.array(z.string()).optional(),
  strategy: z.enum(['narrow', 'wide', 'dynamic']).default('dynamic'),
  narrowRangePct: z.number().min(0.001).max(0.5).default(0.08),
  wideRangePct: z.number().min(0.01).max(1.0).default(0.15),
  volLookbackHours: z.number().min(0.5).max(24).default(2),
  volTickWidthMin: z.number().int().min(60).default(480),
  volTickWidthMax: z.number().int().min(60).default(1200),
  volScalingMode: z.enum(['tiered', 'continuous']).default('continuous'),
  sigmaLow: z.number().min(1).max(200).default(40),
  sigmaHigh: z.number().min(1).max(500).default(120),
  regimeEnabled: z.boolean().default(true),
  binanceVolFallback: z.boolean().default(false),
})

export const ConfigSchema = z.object({
  network: z.enum(['mainnet', 'testnet']).default('testnet'),
  privateKey: z.string().min(1, 'SUI_PRIVATE_KEY is required'),
  pools: z.array(PoolConfigSchema).min(1, 'At least one pool ID is required'),
  rebalanceThreshold: z.number().min(0).max(1).default(0.03),
  harvestIntervalSec: z.number().int().min(60).default(7200),
  checkIntervalSec: z.number().int().min(10).default(30),
  slippageTolerance: z.number().min(0).max(0.5).default(0.01),
  minGasProfitRatio: z.number().min(1).default(2),
  logLevel: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
  dryRun: z.boolean().default(true),
  harvestThresholdUsd: z.number().min(0).default(0.50),
  maxSwapCostPct: z.number().min(0).max(0.5).default(0.01),
  swapFreeRebalance: z.boolean().default(true),
  swapFreeMaxRatioSwap: z.number().min(0).max(0.5).default(0),
  maxIdleSwapRatio: z.number().min(0).max(1).default(0.20),
  fallbackDailyVolumeRatio: z.number().min(0.001).max(0.5).default(0.02).optional(),
  maxBreakevenHours: z.number().int().min(1).max(168).default(48).optional(),
  rebalanceFreeHarvestUsd: z.number().min(0).default(3.0),
})

export type PoolConfig = z.infer<typeof PoolConfigSchema>
export type Config = z.infer<typeof ConfigSchema>
