!ifndef CAPTIONNEST_MODEL_RETENTION_POLICY
!define CAPTIONNEST_MODEL_RETENTION_POLICY

; Tauri uses 1 for an older installed version and 2 for the second radio
; button (install in place). This default prevents a vulnerable old
; uninstaller from running during the first upgrade to a fixed version.
!macro CAPTIONNEST_SET_REINSTALL_DEFAULT VersionComparison Selection
  ${If} ${Selection} == ""
    ${If} ${VersionComparison} = 1
      StrCpy ${Selection} 2
    ${Else}
      StrCpy ${Selection} 1
    ${EndIf}
  ${EndIf}
!macroend

; Keep the decision itself independent of UI so the exact policy can be
; exercised by a silent NSIS harness without displaying installer windows.
!macro CAPTIONNEST_SKIP_UNINSTALL_FOR_MODE SilentMode PassiveMode DoneLabel
  ${If} ${SilentMode} = 1
    Goto ${DoneLabel}
  ${EndIf}
  ${If} ${PassiveMode} = 1
    Goto ${DoneLabel}
  ${EndIf}
!macroend

; Unattended installs cannot collect a destructive data-deletion decision.
; They therefore install in place instead of invoking the old uninstaller.
Var CaptionNestSilentMode
!macro CAPTIONNEST_SKIP_UNINSTALL_FOR_UNATTENDED PassiveMode DoneLabel
  StrCpy $CaptionNestSilentMode 0
  IfSilent 0 +2
  StrCpy $CaptionNestSilentMode 1
  !insertmacro CAPTIONNEST_SKIP_UNINSTALL_FOR_MODE \
    $CaptionNestSilentMode ${PassiveMode} ${DoneLabel}
!macroend

!endif
