; installer.nsh — InkCube custom NSIS hooks (electron-builder nsis.include).
;
; 卸载数据询问（0.2 §3，打包/卸载复盘 2026-10-25 #3）：
; deleteAppDataOnUninstall 保持 false（默认保留，用户数据不随卸载丢失），
; 删不删改由卸载时询问。静默卸载（/S）默认保留（/SD IDNO）。
; 边界：perMachine 安装下卸载器以管理员运行，$APPDATA 展开为运行卸载器
; 的用户目录——单用户桌面场景（绝大多数）无差异。
; $APPDATA\<productName> 与 Electron userData 同源，随 productName 变化；
; 下方 RMDir 路径与 electron-builder.yml 的 productName 必须同步修改。
;
; 文案不用 LangString：electron-builder 的 assisted installer 内置数十种
; 语言，LangString 未覆盖的语言表项会触发 warning 6040（electron-builder
; 把 warning 当 error）；改用 $LANGUAGE 运行时判断 + 双语字面量。
; 本文件经 makensis 以 UTF-8 处理（Unicode 安装器），中英文字面量安全。
!include "LogicLib.nsh"

!macro customUnInstall
  ; 中文变体：zh-CN 2052 / zh-TW 1028 / zh-HK 3076 / zh-SG 4100 / zh-MO 5124
  ${If} $LANGUAGE = 2052
  ${OrIf} $LANGUAGE = 1028
  ${OrIf} $LANGUAGE = 3076
  ${OrIf} $LANGUAGE = 4100
  ${OrIf} $LANGUAGE = 5124
    MessageBox MB_YESNO|MB_ICONQUESTION "是否同时删除用户数据（$APPDATA\InkCube 下的设置、任务、知识库、日志等）？$\n$\n选择「否」保留数据，重装后可继续使用。" /SD IDNO IDNO +2
    RMDir /r "$APPDATA\InkCube"
  ${Else}
    MessageBox MB_YESNO|MB_ICONQUESTION "Also delete user data in $APPDATA\InkCube (settings, tasks, knowledge base, logs)?$\n$\nChoose No to keep it for a future reinstall." /SD IDNO IDNO +2
    RMDir /r "$APPDATA\InkCube"
  ${EndIf}
!macroend
