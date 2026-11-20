/**
 * 워크벤치 기능의 공개 진입점. 다른 곳은 이 파일로만 가져온다.
 *
 * 검수 워크벤치, 클립 뷰어, 완료 보기가 라벨링 워크벤치와 같은 머리 줄·확인창·가이드·단축키·문장 부품을 쓴다.
 * 한 곳의 부품을 나눠 써서 워크벤치들의 모양과 키를 같게 유지한다.
 * 라벨링 워크벤치 화면과 완료 워크벤치 화면은 라우터만 쓰므로 내놓지 않는다(`routes.tsx`).
 */
export { CaptionBody } from "./components/CaptionView";
export { REASON_CHIP, WB_CHIP, WB_CHIP_TIGHT } from "./components/chip";
export { GuidePanel } from "./components/GuidePanel";
export { Keycaps } from "./components/Keycaps";
export { CAPTION_FONT_SIZE, WbSaveStatus, WbVideoMeta } from "./components/label_parts";
export { ReportDeidentModal } from "./components/ReportDeidentModal";
export { SentenceTriage, type SentenceMark } from "./components/SentenceTriage";
export { WbShortcutsDialog } from "./components/WbShortcutsDialog";
export { WorkbenchConfirm } from "./components/WorkbenchConfirm";
export { WorkbenchHeader, type WbAction, type WbStat } from "./components/WorkbenchHeader";
export {
  finalText,
  joinSentences,
  liveSentences,
  sentencesOf,
  toSentences,
  type CaptionSentence,
} from "./lib/caption";
export { estimateTokens } from "./lib/tokens";
export { useVideoShortcuts, VIDEO_SHORTCUTS } from "./lib/useVideoShortcuts";
export { useWorkbenchSurface } from "./store";
