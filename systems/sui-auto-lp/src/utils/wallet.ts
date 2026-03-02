import { Ed25519Keypair } from '@mysten/sui/keypairs/ed25519'
import { getLogger } from './logger.js'

export function loadKeypair(privateKeyBase64: string): Ed25519Keypair {
  const log = getLogger()

  // Support both raw base64 and suiprivkey bech32 format
  if (privateKeyBase64.startsWith('suiprivkey')) {
    const keypair = Ed25519Keypair.fromSecretKey(privateKeyBase64)
    log.info('Wallet loaded (bech32 format)', { address: keypair.getPublicKey().toSuiAddress() })
    return keypair
  }

  const secretKey = Buffer.from(privateKeyBase64, 'base64')
  // Sui keys are 32 bytes; some exports prepend a 1-byte scheme flag
  const raw = secretKey.length === 33 ? secretKey.subarray(1) : secretKey
  const keypair = Ed25519Keypair.fromSecretKey(raw)
  log.info('Wallet loaded (base64 format)', { address: keypair.getPublicKey().toSuiAddress() })
  return keypair
}
