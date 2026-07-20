import { openFolder } from '../api/client'

export async function openOutputFolder(
  path: string | null | undefined,
  onError: (message: string | null) => void,
) {
  if (!path) return
  try {
    await openFolder(path)
    onError(null)
  } catch (error) {
    onError(error instanceof Error ? error.message : '无法打开输出目录')
  }
}
