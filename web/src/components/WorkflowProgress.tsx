import { Check, LoaderCircle } from 'lucide-react'

import type { JobView } from '../types/api'

const STEPS = [
  { key: 'extract', label: '提取音频', aliases: ['queued', 'extract', 'extracting_audio'] },
  { key: 'transcribe', label: '语音识别', aliases: ['transcribe', 'transcribing', 'recognition'] },
  { key: 'translate', label: '翻译字幕', aliases: ['translate', 'translating', 'write', 'writing'] },
] as const

function activeStep(job: JobView | null) {
  if (!job) return -1
  if (job.status === 'completed') return STEPS.length
  const stage = (job.stage ?? job.status).toLowerCase()
  const index = STEPS.findIndex((step) => step.aliases.some((alias) => stage.includes(alias)))
  return index >= 0 ? index : 0
}

interface WorkflowProgressProps {
  job: JobView | null
}

export function WorkflowProgress({ job }: WorkflowProgressProps) {
  const active = activeStep(job)
  const isFailed = job?.status === 'failed'

  return (
    <section className="workflow-panel" aria-label="任务阶段">
      <ol className="workflow-steps">
        {STEPS.map((step, index) => {
          const complete = active > index
          const current = active === index && !isFailed
          return (
            <li
              key={step.key}
              className={`${complete ? 'is-complete' : ''} ${current ? 'is-current' : ''}`}
              aria-current={current ? 'step' : undefined}
            >
              <div className="step-rail">
                <span className="step-number">
                  {complete ? <Check size={16} aria-hidden="true" /> : current ? <LoaderCircle size={16} className="is-spinning" aria-hidden="true" /> : index + 1}
                </span>
                {index < STEPS.length - 1 ? <span className="step-line" aria-hidden="true" /> : null}
              </div>
              <strong>{step.label}</strong>
              <span>{complete ? '已完成' : current ? '处理中' : '等待中'}</span>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
