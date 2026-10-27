import { ApiError } from './api'

export function isRecoveryHold(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 503 && failure.code === 'recovery_hold'
}

export const recoveryHoldMessage = '服务因数据恢复对账暂停。请联系管理员，完成授权核对后再刷新；无需修改账号密码。'
