import { expect, it } from 'vitest'
import { ApiError } from './api'
import { isRecoveryHold, recoveryHoldMessage } from './auth-errors'

it('recognizes only the explicit 503 recovery hold', () => {
  expect(isRecoveryHold(new ApiError('held', 503, 'recovery_hold'))).toBe(true)
  expect(recoveryHoldMessage).toContain('无需修改账号密码')
})
it('does not mistake rate limiting or expired identity for maintenance', () => {
  expect(isRecoveryHold(new ApiError('limited', 429))).toBe(false)
  expect(isRecoveryHold(new ApiError('expired', 401, 'recovery_hold'))).toBe(false)
})
it('does not treat an arbitrary service outage as proof of a recovery hold', () => {
  expect(isRecoveryHold(new ApiError('unavailable', 503))).toBe(false)
})
it('does not trust a plain object or message string as a backend hold', () => {
  expect(isRecoveryHold({ status: 503, code: 'recovery_hold' })).toBe(false)
  expect(isRecoveryHold('recovery_hold')).toBe(false)
})
