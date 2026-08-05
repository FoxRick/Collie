; Collie - friendly per-user Windows setup wizard.
;
; This file is the `nsis.include` for electron-builder. It is injected at the
; TOP of the generated NSIS script, before the electron-builder template, so
; the macros and defines below are visible to assistedInstaller.nsh.
;
; Page flow (fresh install):
;   1. Welcome          - branding + one warm line (MUI2 page, custom copy)
;   2. Install mode     - Recommended (per-user, no admin) vs Custom location
;   3. Location         - only when Custom was chosen (MUI2 directory page)
;   4. Shortcuts        - one checkbox: desktop shortcut, default ON
;   5. Progress         - MUI2 install-files page (template)
;   6. Finish           - "Start Collie" checked, warm closing line
;
; IMPORTANT: every function that calls a plugin (nsDialogs, StdUtils) must be
; defined INSIDE the page-hook macros below, never at this file's top level.
; electron-builder registers its plugin directory from an async task that can
; land AFTER this include in the generated script, so plugin calls in
; top-level code fail with "Plugin not found". Macro bodies only expand later,
; inside the template, when the plugin directory is already registered.
;
; Voice rules (PR #19): Collie speaks like a warm friendly person. Never
; dog-voiced text, never jargon ("installer", "binary", "PATH" never appear).
; Copy is plain, short, and ASCII-only (avoids NSIS codepage surprises).

!include "nsDialogs.nsh"

; ---- Warm branding: small header image on every page (left side) ----
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "${BUILD_RESOURCES_DIR}/installer-header.bmp"
!define MUI_HEADERIMAGE_BITMAP_NOSTRETCH
!define MUI_HEADERIMAGE_UNBITMAP "${BUILD_RESOURCES_DIR}/installer-header.bmp"
!define MUI_HEADERIMAGE_UNBITMAP_NOSTRETCH

; Shared page state
Var InstallMode                  ; "recommended" | "custom" | "" (update/silent)
Var InstallModeDialog
Var InstallModeRecommendedRadio
Var InstallModeCustomRadio
Var ShortcutsDecided             ; "1" once the shortcuts page was shown
Var DesktopShortcutOn            ; "1" when the desktop checkbox is checked
Var ShortcutsDialog
Var DesktopShortcutCheckbox

; ---------------------------------------------------------------------------
; Page 1 + 2: Welcome, then Install mode.
; ---------------------------------------------------------------------------
!macro customWelcomePage
  !define MUI_WELCOMEPAGE_TITLE "Welcome to Collie"
  !define MUI_WELCOMEPAGE_TEXT "Collie is your personal AI - free, open source (MIT), no account needed.$\r$\n$\r$\nCollie lives on your computer and is ready whenever you are. A few friendly pages and you are done."
  !insertmacro MUI_PAGE_WELCOME

  Function InstallModePageCreate
    ${if} ${isUpdated}
      Abort
    ${endif}

    !insertmacro MUI_HEADER_TEXT "How would you like to set up Collie?" "Two easy choices - most people pick the first one."
    nsDialogs::Create 1018
    Pop $InstallModeDialog
    ${If} $InstallModeDialog == error
      Abort
    ${EndIf}

    ${NSD_CreateLabel} 0u 0u 100% 30u "The recommended way needs no administrator password and puts Collie in the best spot automatically."
    Pop $0

    ${NSD_CreateRadioButton} 10u 38u 280u 12u "Set up Collie for me (recommended)"
    Pop $InstallModeRecommendedRadio
    ${NSD_CreateRadioButton} 10u 58u 280u 12u "Choose where to put Collie"
    Pop $InstallModeCustomRadio

    ${NSD_Check} $InstallModeRecommendedRadio
    nsDialogs::Show
  FunctionEnd

  Function InstallModePageLeave
    ${NSD_GetState} $InstallModeRecommendedRadio $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $InstallMode "recommended"
    ${Else}
      StrCpy $InstallMode "custom"
    ${EndIf}
  FunctionEnd

  Page custom InstallModePageCreate InstallModePageLeave

  ; The template's directory page follows; skip it unless Custom was chosen.
  !define MUI_PAGE_CUSTOMFUNCTION_SHOW DirectoryPageShow
!macroend

; ---------------------------------------------------------------------------
; Page 3 (template directory page): only reached in Custom mode.
; ---------------------------------------------------------------------------
!define MUI_DIRECTORYPAGE_TEXT_TOP "Where should Collie live?$\r$\n$\r$\nCollie keeps everything in one small folder - nothing else on your computer is touched."
!define MUI_DIRECTORYPAGE_TEXT_DESTINATION "Collie's folder"

Function DirectoryPageShow
  ${ifNot} $InstallMode == "custom"
    Abort
  ${endif}
FunctionEnd

; ---------------------------------------------------------------------------
; Page 4: Shortcuts - one checkbox, desktop shortcut default ON.
; ---------------------------------------------------------------------------
!macro customPageAfterChangeDir
  Function ShortcutsPageCreate
    ${if} ${isUpdated}
      Abort
    ${endif}

    !insertmacro MUI_HEADER_TEXT "Almost done" "One tiny choice left."
    nsDialogs::Create 1018
    Pop $ShortcutsDialog
    ${If} $ShortcutsDialog == error
      Abort
    ${EndIf}

    ${NSD_CreateLabel} 0u 0u 100% 24u "Collie can sit on your desktop so it is always one double-click away."
    Pop $0

    ${NSD_CreateCheckBox} 10u 40u 280u 12u "Add Collie to my desktop"
    Pop $DesktopShortcutCheckbox
    ${NSD_Check} $DesktopShortcutCheckbox

    nsDialogs::Show
  FunctionEnd

  Function ShortcutsPageLeave
    StrCpy $ShortcutsDecided "1"
    ${NSD_GetState} $DesktopShortcutCheckbox $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $DesktopShortcutOn "1"
    ${Else}
      StrCpy $DesktopShortcutOn "0"
    ${EndIf}
  FunctionEnd

  Page custom ShortcutsPageCreate ShortcutsPageLeave
!macroend

; ---------------------------------------------------------------------------
; Page 6: Finish - "Start Collie" checked by default, warm closing line.
; The template creates the desktop shortcut during install; when the user
; unchecked it here, remove it again right before the finish page shows.
; ---------------------------------------------------------------------------
!macro customFinishPage
  Function FinishPagePre
    ${if} $ShortcutsDecided == "1"
    ${andIfNot} $DesktopShortcutOn == "1"
      Delete "$newDesktopLink"
      ClearErrors
    ${endif}
  FunctionEnd

  Function StartCollie
    ${if} ${isUpdated}
      StrCpy $1 "--updated"
    ${else}
      StrCpy $1 ""
    ${endif}
    ${StdUtils.ExecShellAsUser} $0 "$launchLink" "open" "$1"
  FunctionEnd

  !define MUI_PAGE_CUSTOMFUNCTION_PRE FinishPagePre
  !define MUI_FINISHPAGE_TITLE "Collie is ready to meet you"
  !define MUI_FINISHPAGE_TEXT "That is everything. Collie is on your computer and ready to go.$\r$\n$\r$\nSay hello, then tell Collie what you would like to do. It learns as you go."
  !define MUI_FINISHPAGE_RUN
  !define MUI_FINISHPAGE_RUN_TEXT "Start Collie now"
  !define MUI_FINISHPAGE_RUN_FUNCTION "StartCollie"
  !insertmacro MUI_PAGE_FINISH
!macroend

; ---------------------------------------------------------------------------
; Per-user install, no admin prompt: force current-user mode so the template's
; "who should this be installed for" page is skipped entirely.
; ---------------------------------------------------------------------------
!macro customInstallMode
  StrCpy $isForceCurrentInstall "1"
!macroend
