import winston from 'winston'

let logger: winston.Logger | null = null

export function initLogger(level: string): winston.Logger {
  logger = winston.createLogger({
    level,
    format: winston.format.combine(
      winston.format.timestamp(),
      winston.format.json(),
    ),
    transports: [
      new winston.transports.Console({
        format: winston.format.combine(
          winston.format.colorize(),
          winston.format.printf(({ timestamp, level, message, ...meta }) => {
            const metaStr = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : ''
            return `${timestamp} [${level}] ${message}${metaStr}`
          }),
        ),
      }),
    ],
  })
  return logger
}

export function getLogger(): winston.Logger {
  if (!logger) {
    return initLogger('info')
  }
  return logger
}
