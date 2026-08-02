const PET_COMMANDS = new Set([
  'idle', 'working', 'review', 'completion', 'error',
  'walk', 'sleep', 'happy', 'concerned', 'wave',
  'hide', 'show', 'roam', 'stay', 'quit'
])

export function isAllowedPetCommand(command: string): boolean {
  return PET_COMMANDS.has(command) || /^size:\d+(\.\d+)?$/.test(command) ||
    (command.startsWith('status:') && command.length <= 152)
}
