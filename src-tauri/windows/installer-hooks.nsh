!macro NSIS_HOOK_PREUNINSTALL
  MessageBox MB_ICONEXCLAMATION|MB_OKCANCEL|MB_DEFBUTTON2 "卸载 CaptionNest 将同时删除本机下载的语音识别模型。模型文件可能占用数 GB 空间，删除后无法恢复。$\r$\n$\r$\n选择“确定”继续卸载，选择“取消”保留应用和模型。" IDOK continue_uninstall
  Abort
  continue_uninstall:
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  RMDir /r "$LOCALAPPDATA\io.github.coconilu.captionnest\models"
!macroend
