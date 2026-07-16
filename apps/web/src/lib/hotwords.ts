export const HOTWORD_MAX_ENTRIES = 50
export const HOTWORD_MAX_ENTRY_CHARACTERS = 64
export const HOTWORD_MAX_TOTAL_CHARACTERS = 512

export interface HotwordValidation {
  hotwords: string[]
  characterCount: number
  error: string | null
}

function characterCount(value: string): number {
  return Array.from(value).length
}

function containsForbiddenCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0
    return codePoint < 32
      || codePoint === 127
      || codePoint === 0x2028
      || codePoint === 0x2029
  })
}

export function validateHotwordText(value: string): HotwordValidation {
  const hotwords: string[] = []
  const seen = new Set<string>()
  let totalCharacters = 0
  const lines = value.split(/\r\n?|\n/u)

  for (let index = 0; index < lines.length; index += 1) {
    const hotword = lines[index].trim()
    if (!hotword) continue
    if (containsForbiddenCharacter(hotword)) {
      return {
        hotwords,
        characterCount: totalCharacters,
        error: `第 ${index + 1} 个提示词不能包含换行或控制字符`,
      }
    }
    const itemCharacters = characterCount(hotword)
    if (itemCharacters > HOTWORD_MAX_ENTRY_CHARACTERS) {
      return {
        hotwords,
        characterCount: totalCharacters,
        error: `单个提示词不能超过 ${HOTWORD_MAX_ENTRY_CHARACTERS} 个字符（第 ${index + 1} 项）`,
      }
    }
    if (seen.has(hotword)) continue

    seen.add(hotword)
    hotwords.push(hotword)
    totalCharacters += itemCharacters
    if (hotwords.length > HOTWORD_MAX_ENTRIES) {
      return {
        hotwords,
        characterCount: totalCharacters,
        error: `提示词不能超过 ${HOTWORD_MAX_ENTRIES} 条`,
      }
    }
    if (totalCharacters > HOTWORD_MAX_TOTAL_CHARACTERS) {
      return {
        hotwords,
        characterCount: totalCharacters,
        error: `提示词总字符数不能超过 ${HOTWORD_MAX_TOTAL_CHARACTERS} 个`,
      }
    }
  }

  return { hotwords, characterCount: totalCharacters, error: null }
}
