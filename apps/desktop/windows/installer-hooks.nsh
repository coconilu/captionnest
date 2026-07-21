!include "${__FILEDIR__}\model-retention-policy.nsh"

!ifndef CAPTIONNEST_MODELS_DIR
  !define CAPTIONNEST_MODELS_DIR "$LOCALAPPDATA\io.github.coconilu.captionnest\models"
!endif

!macro NSIS_HOOK_POSTUNINSTALL
  ; Tauri's uninstall confirmation page owns the user decision. Its built-in
  ; "delete app data" checkbox is unchecked by default and is skipped for
  ; passive, silent, and /UPDATE runs. Mirror the same guard here so models are
  ; never removed merely because an older program version is being replaced.
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    RMDir /r "${CAPTIONNEST_MODELS_DIR}"
  ${EndIf}
!macroend
