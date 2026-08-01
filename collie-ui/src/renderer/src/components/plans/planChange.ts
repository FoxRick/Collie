export const PLAN_CHANGE_REQUEST_EVENT = 'collie:change-plan-request'
export const PLAN_CHANGE_RESULT_EVENT = 'collie:change-plan-result'

export type PlanChangeState = 'requesting' | 'pending_safe_boundary' | 'paused' | 'error'

export interface PlanChangeRequest {
  planId: string
  version: number
}

export interface PlanChangeResult extends PlanChangeRequest {
  state: PlanChangeState
  message: string
}

export function requestPlanChange(planId: string, version: number): void {
  window.dispatchEvent(
    new CustomEvent<PlanChangeRequest>(PLAN_CHANGE_REQUEST_EVENT, { detail: { planId, version } })
  )
}

export function publishPlanChangeResult(result: PlanChangeResult): void {
  window.dispatchEvent(new CustomEvent<PlanChangeResult>(PLAN_CHANGE_RESULT_EVENT, { detail: result }))
}
