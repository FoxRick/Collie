// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import ManagedInferenceCard from './ManagedInferenceCard'
let root: Root
let host: HTMLDivElement
const status={configured:true,available:true,signedIn:true,remaining:10,limit:20,resetsAt:null,message:'Included AI'}
async function render(value= status) {
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true})
  const api={inferenceStatus:vi.fn(async()=>value),useInference:vi.fn(async()=>({configured:true})),startSignIn:vi.fn()}
  Object.defineProperty(window,'account',{value:api,configurable:true})
  host=document.createElement('div')
  document.body.append(host)
  root=createRoot(host)
  const activated=vi.fn()
  await act(async()=>{root.render(<ManagedInferenceCard onActivated={activated}/>)})
  return {api,activated}
}
afterEach(async()=>{await act(async()=>root.unmount());host.remove()})
it('disabled builds cannot start sign-in or activate inference',async()=>{
  const {api}=await render({...status,configured:false,available:false,signedIn:false})
  const button=host.querySelector('button')!
  expect(button.disabled).toBe(true)
  expect(api.startSignIn).not.toHaveBeenCalled()
  expect(api.useInference).not.toHaveBeenCalled()
})
it('shows the allowance and activates the managed route',async()=>{
  const {api,activated}=await render()
  expect(host.textContent).toContain('10 / 20 allowance units remaining')
  await act(async()=>host.querySelector('button')!.click())
  expect(api.useInference).toHaveBeenCalledOnce()
  expect(activated).toHaveBeenCalledOnce()
  expect(api.startSignIn).not.toHaveBeenCalled()
})
it('exhausted allowances cannot activate a fallback',async()=>{
  const {api}=await render({...status,available:false,remaining:0})
  expect(host.querySelector('button')!.disabled).toBe(true)
  expect(api.useInference).not.toHaveBeenCalled()
})
