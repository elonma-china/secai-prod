import { createMemo } from "solid-js"
import { useSync } from "../context/sync"
import { useSDK } from "../context/sdk"
import { useToast } from "../ui/toast"
import { DialogSelect } from "../ui/dialog-select"
import { useDialog } from "../ui/dialog"

export function DialogKillSubagent(props: { sessionID: string }) {
  const sync = useSync()
  const sdk = useSDK()
  const dialog = useDialog()
  const toast = useToast()

  const subagents = createMemo(() => {
    const parentID = props.sessionID
    return sync.data.session
      .filter((s) => s.parentID === parentID)
      .map((s) => {
        const status = sync.data.session_status[s.id]
        const running = status?.type === "busy"
        const elapsed = Date.now() - new Date(s.time.created).getTime()
        const mins = Math.floor(elapsed / 60000)
        const secs = Math.floor((elapsed % 60000) / 1000)
        const duration = mins > 0 ? `${mins}m${secs}s` : `${secs}s`
        return {
          value: s.id,
          title: `${s.agent ?? "subagent"}: ${s.title}`,
          description: `${running ? "RUNNING" : status?.type ?? "idle"} · ${duration}`,
          disabled: !running,
        }
      })
      .sort((a, b) => (a.disabled === b.disabled ? 0 : a.disabled ? 1 : -1))
  })

  return (
    <DialogSelect
      title="Kill subagent"
      options={subagents()}
      emptyView={<span>No subagents found for this session</span>}
      onSelect={async (option) => {
        try {
          await sdk.client.session.abort({ sessionID: option.value })
          toast.show({ message: `Subagent killed: ${option.title}`, variant: "info" })
        } catch {
          toast.show({ message: `Failed to kill subagent`, variant: "error" })
        }
        dialog.clear()
      }}
    />
  )
}
