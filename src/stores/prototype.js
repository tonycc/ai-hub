import { reactive } from 'vue'
import { applications, capabilities, commands, semanticObjects } from '../data/mock'

const state = reactive({
  applications: structuredClone(applications),
  capabilities: structuredClone(capabilities),
  commands: structuredClone(commands),
  semanticObjects: structuredClone(semanticObjects),
})

export function usePrototypeStore() {
  function retryCommand(id) {
    const command = state.commands.find((item) => item.id === id)
    if (!command) return
    command.status = 'SUCCEEDED'
    command.attempts += 1
    command.message = '中性认证命令已由前端原型标记为成功'
  }

  return {
    state,
    retryCommand,
  }
}
