/**
 * 자격증명 연결 화면(`/admin/connections`). super_admin 전용이다.
 *
 * 프리셋(조합)은 코드가 정하고, 관리자는 그 조합에 필요한 값(엔드포인트,
 * 버킷, 자격증명)만 이 화면에서 채운다.
 *
 * 시크릿은 쓰기 전용이다. 서버는 시크릿을 돌려주지 않는다(`ConnectionDTO`에 값이 없다).
 * 그래서 이 화면은 시크릿을 넣을 수만 있고 다시 볼 수 없다.
 * 목록에는 자격증명을 넣은 시각(`자격증명 N일 전`)만 보여 준다.
 * 편집할 때 시크릿을 비워 두면 기존 값을 그대로 둔다.
 * 값 하나를 고치려고 시크릿을 다시 넣게 하지 않는다.
 *
 * 저장하면 서버가 같은 요청에서 자격증명을 실제로 호출해 점검한다(연동 진단과 같은 방식).
 * 저장만 하고 써 보지 않으면 "등록은 되는데 분석만 안 되는" 실패를 알아채지 못한다.
 * 점검이 실패해도 연결은 저장되고, 행에 실패 사유와 고치는 방법이 남는다. 그 뒤로는 15분 주기 점검이 갱신한다.
 * 이 동작은 화면 어디서나 `점검` 한 낱말로 부른다.
 *
 * 저장소·분석 연결은 프로젝트 설정의 분석 연동에서 고르면 업로드·분석이 그 자격증명을 읽는다
 * (`services/stacks/connections.py`).
 * 캡션 품질검수는 연결 이름으로 모델 연결을 읽는다(행의 `사용처`에 그 프로젝트가 나온다).
 *
 * 연결은 서비스 종류로 두 묶음(캡션 품질검수 모델, 저장소·분석 서비스)으로 나눠 보여 준다.
 */
import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Eye, EyeOff, Plus, Trash2, TriangleAlert } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { EmptyState } from "@/shared/ui/EmptyState";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Modal } from "@/shared/ui/Modal";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Select } from "@/shared/ui/Select";
import { useToast } from "@/shared/ui/Toast";
import {
  type ConnectionDTO,
  type ConnectionProvider,
  type ConnectionSaved,
} from "@/shared/api/client";
import { inUseProjects, type InUseProjects } from "@/shared/api/client";
import { apiErrorText, type ErrorText } from "@/shared/api/error_text";
import { relativeTime } from "@/shared/lib/time";
import { cn } from "@/shared/lib/cn";

import {
  createConnection,
  deleteConnection,
  fetchConnections,
  platformKeys,
  updateConnection,
} from "../api";
import { bucketListText, parseBucketList, secretRequired } from "../lib/connection_address";
import { MODEL_PROVIDERS, PROVIDER_LABEL, isModelProvider } from "../lib/connection_provider";
import { analysisOffText } from "../lib/analysis_off";
import { inUseProjectsLine } from "../lib/in_use_projects";

/**
 * 고를 수 있는 프로바이더에 붙이는 쓰임새.
 * NCP 저장소와 Media Intelligence는 프로젝트 설정의 분석 연동에서 고르면 업로드·분석이 그 자격증명을 쓴다.
 */
const PROVIDER_NOTE: Partial<Record<ConnectionProvider, string>> = {
  ncp: "프로젝트 저장소로 사용",
  maiu: "프로젝트 분석으로 사용",
};

/**
 * 프로바이더 고르기의 묶음. 무엇에 쓰는 연결인지 묶음 이름으로 알린다.
 * 저장소는 NCP 하나다. 서버도 새 연결로 `s3`·`gcs`를 받지 않는다(`NewConnectionProvider`).
 * 프로젝트를 만들려면 저장소 연결이 먼저 있어야 하므로 저장소·분석 묶음을 앞에 둔다.
 */
const PROVIDER_GROUPS: { label: string; providers: ConnectionProvider[] }[] = [
  { label: "저장소·분석 서비스", providers: ["ncp", "maiu"] },
  { label: "캡션 품질검수 모델", providers: MODEL_PROVIDERS },
];

/**
 * 새 연결의 기본 프로바이더. 처음 설치한 서버에서 가장 먼저 등록하는 NCP 저장소다.
 * 저장소 연결이 없으면 어떤 분석 방식으로도 프로젝트를 만들 수 없다.
 */
const DEFAULT_PROVIDER: ConnectionProvider = "ncp";

/** 이름 입력란의 예시. 고른 서비스에 맞춰 바꾼다. */
function nameExample(p: ConnectionProvider): string {
  if (p === "ncp") return "예: NCP 서울 저장소";
  if (p === "maiu") return "예: Media Intelligence 운영";
  return `예: ${PROVIDER_LABEL[p]} 품질검수`;
}

/**
 * 프로바이더별 비밀이 아닌 입력 필드.
 *
 * 키 이름은 계약 `PROVIDER_FIELDS`(`reborn_contracts.connection`)와 같다.
 * 서버는 `config`를 검증 없이 그대로 저장한다. 그래서 어떤 키가 들어갈지는 이 표가 정한다.
 * 프로바이더를 바꿀 때 쓰지 않는 키를 버리는 것도 화면이 맡는다(아래 `onChange`).
 * `optional`은 비워 두어도 점검과 캡션 품질검수가 도는 필드다(`services/stacks/connections._probe`).
 * `example`은 형식을 보여 주는 예시다. 안내 문장은 placeholder에 두지 않는다.
 * `s3`·`gcs`는 새로 만들 수 없지만 이미 저장된 행을 고칠 때 입력란이 필요해 남긴다.
 * `list`는 이름 목록이다. 쉼표로 나눈 글자로 받고 목록으로 보낸다. 한 줄을 다 쓴다.
 */
type FieldSpec = {
  key: string;
  label: string;
  example?: string;
  optional?: boolean;
  list?: boolean;
};
const PROVIDER_FIELDS: Record<ConnectionProvider, FieldSpec[]> = {
  // 화면 용어는 `저장소`다. `버킷`은 check-jargon이 막는 내부 용어다.
  s3: [
    { key: "endpoint", label: "엔드포인트", example: "예: https://s3.ap-northeast-2.amazonaws.com", optional: true },
    { key: "bucket", label: "저장소", example: "예: reborn-media" },
    { key: "region", label: "리전", example: "예: ap-northeast-2" },
  ],
  ncp: [
    { key: "endpoint", label: "엔드포인트", example: "예: https://kr.object.ncloudstorage.com" },
    { key: "bucket", label: "저장소", example: "예: reborn-media" },
    { key: "region", label: "리전", example: "예: kr-standard" },
    // 프로젝트 관리자가 저장소 대상으로 고를 수 있는 범위다(`project_storage.allowed_buckets`).
    // 키는 보통 계정의 모든 저장소에 닿으므로 범위를 연결 편집 권한(최고관리자)으로만 넓힌다.
    // 프로젝트가 쓰는 저장소를 범위에서 빼는 저장은 서버가 409 `allowed_bucket_in_use`로 거절한다.
    {
      key: "allowed_buckets", label: "허용 저장소", example: "예: reborn-media-2, reborn-archive",
      optional: true, list: true,
    },
  ],
  gcs: [{ key: "bucket", label: "저장소", example: "예: reborn-media" }],
  // 분석 프로젝트는 프로젝트마다 분석 연동에서 고른다. 연결에는 점검할 워크스페이스만 둔다.
  // API 주소는 공식 주소를 쓰면 비운다.
  maiu: [
    { key: "base_url", label: "API 주소", example: "예: https://mi.apigw.ntruss.com", optional: true },
    { key: "workspace_name", label: "워크스페이스" },
  ],
  // 캡션 검수 모델
  // `model`은 비밀 값이 아닌 설정이다. 어느 모델이 판정했는지 기록에 남아야 한다.
  // `base_url`은 공식 엔드포인트를 쓰면 비운다.
  // 화면 이름은 `API 주소`다. `베이스 URL`은 영어를 옮긴 말이다.
  // `엔드포인트`는 클라우드 콘솔의 입력란 이름이라 그대로 둔다(`check-jargon` 예외).
  openai: [
    { key: "model", label: "모델" },
    { key: "base_url", label: "API 주소", example: "예: https://api.openai.com/v1", optional: true },
  ],
  anthropic: [{ key: "model", label: "모델" }],
  "local-llm": [
    { key: "base_url", label: "API 주소", example: "예: http://10.0.0.5:8000/v1" },
    { key: "model", label: "모델" },
  ],
  // Bedrock은 리전에 따라 쓸 수 있는 모델이 다르다(같은 모델이 리전마다 없을 수 있다).
  // 모델은 ID를 직접 입력하고, 점검이 그 리전의 모델 목록에서 찾는다.
  bedrock: [
    { key: "region", label: "리전", example: "예: us-east-1" },
    { key: "model", label: "모델", example: "예: anthropic.claude-3-haiku-20240307-v1:0" },
  ],
};

/**
 * 프로바이더별 자격증명 입력. 서버는 두 값을 한 문자열로 저장한다(`services/stacks/connections._pack_secret`).
 * `pair`는 액세스 키와 시크릿 키 두 값이다.
 * `api_key`는 API 키 하나이고 앞 값(`access_key`)으로 보낸다. 캡션 품질검수 실행기가 앞 값을
 * API 키로 쓰기 때문이다(`caption_qc.runner`의 `ModelConfig.api_key`).
 * `optional`은 키 없이 접속하는 배포(사내 모델)가 있어 비워 둘 수 있다는 뜻이다.
 * `required`는 두 키가 모두 저장돼 있어야 하는 서비스다. 서버가 키 없는 연결을 422(`bedrock_keys_required`)로 거절한다.
 * Bedrock은 키가 없으면 서버의 기본 AWS 자격증명으로 접속하게 되므로 키를 받는다.
 */
const CREDENTIAL: Record<ConnectionProvider, { kind: "pair" | "api_key" | "none"; optional?: boolean; required?: boolean }> = {
  s3: { kind: "pair" },
  ncp: { kind: "pair" },
  gcs: { kind: "none" },
  maiu: { kind: "pair" },
  openai: { kind: "api_key" },
  anthropic: { kind: "api_key" },
  "local-llm": { kind: "api_key", optional: true },
  bedrock: { kind: "pair", required: true },
};

/**
 * 연결 행의 설정 키 라벨을 돌려준다. 그 서비스의 `PROVIDER_FIELDS` 라벨을 쓴다.
 * 폼과 목록이 같은 값을 다른 이름으로 부르면 관리자가
 * 무엇을 고쳤는지 알 수 없다.
 * 같은 키(`base_url`)를 서비스마다 다르게 부르므로 키만으로 찾지 않는다. 표에 없는 키는 원래 이름으로 남긴다.
 */
const fieldLabel = (p: ConnectionProvider, key: string): string =>
  PROVIDER_FIELDS[p]?.find((f) => f.key === key)?.label ?? key;

/** 연결 행의 설정값 글자. 목록은 쉼표로 이은 글자, 빈 목록은 `없음`으로 쓴다. */
const fieldValue = (v: unknown): string =>
  Array.isArray(v) ? (v.length ? bucketListText(v) : "없음") : String(v);

/** 저장할 설정값을 만든다. 목록 입력란의 글자는 이름 목록으로 바꾼다. */
const configBody = (p: ConnectionProvider, config: Record<string, string>): Record<string, unknown> =>
  Object.fromEntries(Object.entries(config).map(([k, v]) => [
    k, PROVIDER_FIELDS[p].find((f) => f.key === k)?.list ? parseBucketList(v) : v,
  ]));

/** 자격증명이 필요한 서비스이면 true. 자격증명 입력이 없는 서비스에 `자격증명 필요`를 붙이지 않는다. */
const needsCredential = (p: ConnectionProvider): boolean => CREDENTIAL[p].kind !== "none";

/**
 * 점검 결과의 이름과 색. 행이 늘 표시한다.
 * `warn`은 플랫폼 개요의 연동 점검과 같은 `주의`다. 같은 판정을 화면마다 다르게 부르지 않는다.
 * 자격증명이 없는 경우는 별도 칩(`자격증명 필요`)이 알려 준다.
 */
const HEALTH_LABEL: Record<string, string> = { ok: "정상", warn: "주의", fail: "실패", none: "점검 전" };
const HEALTH_TONE: Record<string, string> = {
  ok: "text-label", warn: "text-warn-text", fail: "text-bad-text", none: "text-label-2",
};
/**
 * 이 연결을 쓰는 프로젝트 이름 목록을 돌려준다.
 *
 * 계약상 선택 필드다(pydantic `default_factory`라 OpenAPI가 required로 내지 않는다).
 * 서버는 늘 보내지만 화면은 그 가정에 기대지 않는다.
 * 값이 없으면 빈 목록으로 본다.
 */
const usedBy = (c: ConnectionDTO): string[] => c.used_by ?? [];

/**
 * 이름 옆 점의 색. 훑어볼 때 가장 먼저 보이는 신호다. `none`은 점검 전 상태이고 정상과 구분한다.
 */
const HEALTH_DOT: Record<string, string> = {
  ok: "bg-ok", warn: "bg-warn", fail: "bg-bad", none: "bg-label-4",
};

/** 연결 묶음. 묶음 이름은 연결 추가 창의 서비스 묶음과 같은 이름이다. */
const LIST_GROUPS: { key: string; label: string; match: (p: ConnectionProvider) => boolean }[] = [
  { key: "model", label: "캡션 품질검수 모델", match: isModelProvider },
  { key: "storage", label: "저장소·분석 서비스", match: (p) => !isModelProvider(p) },
];

/** 자격증명 연결 화면을 그린다. 연결 목록을 서비스 묶음별로 보여 준다. */
export function ConnectionsPage() {
  // 생성 버튼이 머리(`PageHeader`)에 있으므로 편집·생성 상태는 페이지에 둔다.
  const [editing, setEditing] = useState<ConnectionDTO | "new" | null>(null);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="자격증명 연결"
        actions={
          <Button onClick={() => setEditing("new")}>
            <Plus size={14} strokeWidth={2.5} aria-hidden="true" />
            연결 추가
          </Button>
        }
      />
      <ConnectionList editing={editing} setEditing={setEditing} />
    </div>
  );
}

/**
 * 연결 목록을 서비스 묶음별 카드로 그린다.
 * 머리(제목, 생성 버튼)와 편집 상태는 `ConnectionsPage`에 있다.
 */
function ConnectionList({ editing, setEditing }: {
  editing: ConnectionDTO | "new" | null;
  setEditing: (v: ConnectionDTO | "new" | null) => void;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  /*
    삭제는 확인 창을 거친다. 비밀 키는 돌려받을 수 없으므로 지운 자격증명은 되살릴 수 없다.
    되돌릴 수 없는 동작에는 판단 근거(사용처)를 같은 창에 둔다.
  */
  const [deleting, setDeleting] = useState<ConnectionDTO | null>(null);
  /*
    목록을 받은 뒤 다른 사람이 연결을 프로젝트에 고르면 삭제가 409 `connection_in_use`로 거절된다.
    거절 응답의 프로젝트 이름으로 창을 사용 중 상태로 바꿔, 먼저 바꿀 프로젝트를 같은 창에 보인다.
  */
  const [blocked, setBlocked] = useState<{ id: string; projects: InUseProjects } | null>(null);
  /*
    저장 직후 점검이 남긴 고치는 방법. 서버는 사유(`last_detail`)만 행에 남기고 방법(`hint`)은 저장 응답에만 싣는다.
    방금 저장한 사람에게만 붙이고, 같은 연결을 다시 저장하면 새 결과로 바꾼다.
  */
  const [hints, setHints] = useState<Record<string, string>>({});

  const list = useQuery({
    queryKey: platformKeys.connections(),
    queryFn: fetchConnections,
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteConnection(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: platformKeys.connections() });
      setDeleting(null);
      toast("success", "연결을 삭제했습니다");
    },
    // 409 `connection_in_use`에 막는 프로젝트가 실려 오면 창을 닫지 않고 사용 중 안내로 바꾼다.
    onError: (e, id) => {
      void qc.invalidateQueries({ queryKey: platformKeys.connections() });
      const projects = (e as { detail?: unknown } | null)?.detail === "connection_in_use" ? inUseProjects(e) : null;
      if (projects) {
        setBlocked({ id, projects });
        return;
      }
      setDeleting(null);
      const t = apiErrorText(e, "삭제 실패");
      toast("error", t.message, t.hint);
    },
  });

  /*
    실패한 연결을 위로 올린다. 서버가 주는 순서는 이름 순이다.
    같은 상태 안에서는 원래 순서를 지킨다. 이유 없이 위치가 바뀌면 방금 본 행을 다시 찾아야 한다.
    점검 전(`null`)은 장애는 아니지만 확인이 필요해서 실패와 정상 사이에 둔다.
  */
  const HEALTH_RANK: Record<string, number> = { fail: 0, warn: 1 };
  const rows = [...(list.data ?? [])].sort(
    (a, b) => (HEALTH_RANK[a.last_status ?? ""] ?? (a.last_status ? 3 : 2))
            - (HEALTH_RANK[b.last_status ?? ""] ?? (b.last_status ? 3 : 2)),
  );

  return (
    <div className="flex flex-col gap-6">

      {/* 불러오지 못한 상태와 연결이 없는 상태를 구분한다. 있는 연결을 없다고 표시하면 관리자가 다시 만든다. */}
      {list.isError ? (
        <ErrorState message="연결 목록 조회 실패" onRetry={() => void list.refetch()} />
      ) : list.isLoading ? (
        <p className="text-footnote text-label-3">연결을 불러오는 중…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          framed
          message="연결 없음"
          action={<Button variant="secondary" size="sm" onClick={() => setEditing("new")}>연결 추가</Button>}
        />
      ) : (
        LIST_GROUPS.map((g) => {
          const items = rows.filter((c) => g.match(c.provider));
          if (items.length === 0) return null;
          return (
            <section key={g.key} aria-labelledby={`group-${g.key}`}>
              <div className="mb-3">
                <h2 id={`group-${g.key}`} className="flex items-baseline gap-2 text-headline text-label">
                  {g.label}
                  <span className="text-caption font-normal text-label-3">{items.length.toLocaleString("ko-KR")}개</span>
                </h2>
              </div>
              <Card>
                <ul className="divide-y divide-separator">
                  {items.map((c) => (
                    <ConnectionRow
                      key={c.id} conn={c} hint={hints[c.id]}
                      onEdit={() => setEditing(c)}
                      onDelete={() => setDeleting(c)}
                    />
                  ))}
                </ul>
              </Card>
            </section>
          );
        })
      )}

      {/* 삭제 확인 창. 사용 중이면 먼저 할 일을, 아니면 무엇이 지워지는지를 같은 창에 보여 준다. */}
      {deleting && (() => {
        const refused = blocked?.id === deleting.id ? blocked.projects : null;
        const users = refused ? refused.names : usedBy(deleting);
        const userCount = users.length + (refused?.more ?? 0);
        const inUse = users.length > 0;
        const close = () => { setDeleting(null); setBlocked(null); };
        return (
          <Modal
            open
            onClose={close}
            title="연결 삭제"
            icon={inUse ? <Ban size={16} aria-hidden="true" /> : <TriangleAlert size={16} aria-hidden="true" />}
            tone={inUse ? "warn" : "danger"}
            footer={inUse ? (
              // 삭제할 수 없는 상태에서는 누를 수 없는 삭제 버튼을 두지 않는다.
              <Button size="sm" variant="secondary" onClick={close}>닫기</Button>
            ) : (
              <>
                <Button size="sm" variant="secondary" onClick={close}>취소</Button>
                <Button
                  size="sm"
                  variant="danger"
                  disabled={remove.isPending}
                  // 요청이 끝날 때까지 창을 닫지 않는다. 닫으면 진행 중인지 보이지 않고 다시 누를 수 있다.
                  onClick={() => remove.mutate(deleting.id)}
                >
                  {remove.isPending ? "삭제 중…" : "삭제"}
                </Button>
              </>
            )}
          >
            <p className="-mt-2 mb-4 text-footnote text-label-3">
              {deleting.name} · {PROVIDER_LABEL[deleting.provider]}
            </p>
            {inUse ? (
              /*
                사용 중인 연결은 지우지 않는다. 서버도 409 `connection_in_use`로 막는다.
                프로젝트 이름은 칩으로 나눈다. 이어 붙이면 줄바꿈이 이름을 자른다.
                서버가 싣지 않은 나머지 프로젝트는 수로 적는다.
              */
              <>
                <p className="text-body font-semibold text-label">사용 중인 연결입니다</p>
                <div className="mt-3 rounded-card bg-canvas-2 px-3.5 py-3">
                  <p className="text-caption text-label-3">
                    {`이 연결을 쓰는 프로젝트 ${userCount.toLocaleString("ko-KR")}개`}
                  </p>
                  <p className="mt-2 flex flex-wrap gap-1.5">
                    {users.map((n) => <Chip key={n} outlined className="font-normal">{n}</Chip>)}
                    {refused && refused.more > 0 && (
                      <span className="self-center text-caption text-label-3">{`외 ${refused.more.toLocaleString("ko-KR")}개`}</span>
                    )}
                  </p>
                </div>
                <p className="mt-3 text-footnote text-label-2">
                  {isModelProvider(deleting.provider)
                    ? "각 프로젝트 설정의 캡션 품질검수에서 다른 연결로 바꾼 뒤 삭제합니다."
                    : "각 프로젝트 설정의 분석 연동에서 다른 연결로 바꾼 뒤 삭제합니다."}
                </p>
              </>
            ) : (
              <>
                <p className="text-body font-semibold text-bad-text">되살릴 수 없습니다</p>
                <p className="mt-1.5 text-footnote text-label-2">
                  {deleting.has_secret
                    ? "설정값과 자격증명이 함께 지워집니다. 비밀 키는 다시 볼 수 없어 발급처에서 새로 받아야 합니다."
                    : "설정값이 지워집니다."}{" "}
                  이 연결을 쓰는 프로젝트는 없습니다.
                </p>
              </>
            )}
          </Modal>
        );
      })()}

      {editing && (
        <ConnectionForm
          conn={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            void qc.invalidateQueries({ queryKey: platformKeys.connections() });
            setHints((h) => ({ ...h, [saved.id]: analysisOffText(saved.check).hint ?? "" }));
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

/**
 * 연결 한 줄을 그린다. 고정 열(이름·서비스·상태, 설정값, 사용처, 동작)로 두어 행마다 줄이 맞는다.
 * 실패 원인은 행 안 한 줄 띠로, 점검·등록·변경 시각은 맨 아래 흐린 한 줄로 둔다.
 * `hint`는 방금 저장한 연결의 점검이 알려 준 고치는 방법이다.
 */
function ConnectionRow({
  conn, hint, onEdit, onDelete,
}: { conn: ConnectionDTO; hint?: string; onEdit: () => void; onDelete: () => void }) {
  const cfg = Object.entries((conn.config ?? {}) as Record<string, unknown>);
  const status = conn.last_status ?? "none";
  const users = usedBy(conn);
  return (
    <li className="px-5 py-4">
      <div className="grid gap-x-6 gap-y-3 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1.6fr)_minmax(0,1.1fr)_auto]">
        <div className="min-w-0">
          <p className="truncate text-body font-semibold text-label">{conn.name}</p>
          <p className="mt-0.5 text-caption text-label-3">{PROVIDER_LABEL[conn.provider]}</p>
          {/*
            상태는 점과 글자로 표시한다. 점만으로는 색각 이상이 있는 사람에게 전달되지 않는다.
            자격증명이 없으면 점검은 늘 같은 사유를 돌려주므로 `자격증명 필요` 하나로 표시한다.
          */}
          <p className="mt-2 flex flex-wrap items-center gap-2">
            <span className={cn("inline-flex items-center gap-1.5 text-footnote font-semibold", HEALTH_TONE[status])}>
              <span aria-hidden="true" className={cn("size-1.5 rounded-capsule", HEALTH_DOT[status])} />
              {HEALTH_LABEL[status] ?? status}
            </span>
            {needsCredential(conn.provider) && !conn.has_secret && <Chip tone="warn">자격증명 필요</Chip>}
          </p>
          {/*
            정상일 때도 점검이 남긴 상세(모델 이름 등)는 판단 근거라 상태 아래 한 줄로 둔다.
            실패·주의 사유는 아래 띠에 둔다.
          */}
          {status === "ok" && conn.last_detail && (
            <p className="mt-1 text-caption text-label-2">{conn.last_detail}</p>
          )}
        </div>

        {/*
          비밀이 아닌 값은 그대로 보여 준다. 어느 저장소인지가 판단 근거가 된다.
          키 이름 대신 폼과 같은 한국어 라벨을 쓰고, 사전에 없는 키는 원래 이름으로 남긴다.
        */}
        <dl className="grid min-w-0 grid-cols-[88px_minmax(0,1fr)] content-start gap-x-3 gap-y-1">
          {cfg.length === 0 ? (
            <span className="col-span-2 text-caption text-label-3">설정값 없음</span>
          ) : (
            cfg.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-caption text-label-3">{fieldLabel(conn.provider, k)}</dt>
                <dd className="truncate font-mono text-caption text-label" title={fieldValue(v)}>{fieldValue(v)}</dd>
              </div>
            ))
          )}
        </dl>

        {/* 사용처가 없는 연결은 사용처가 없다고 적는다. */}
        <div className="min-w-0">
          <p className="text-caption text-label-3">사용처</p>
          {users.length > 0 ? (
            <p className="mt-1 flex flex-wrap gap-1.5">
              {users.map((n) => <Chip key={n} className="font-normal">{n}</Chip>)}
            </p>
          ) : (
            <p className="mt-1 text-caption text-label-2">없음</p>
          )}
        </div>

        <div className="flex items-start gap-1.5">
          {/* 같은 이름의 버튼이 행마다 반복되므로 스크린리더에는 대상을 붙여 읽힌다. */}
          <Button size="sm" variant="secondary" onClick={onEdit} aria-label={`${conn.name} 편집`}>편집</Button>
          <Button size="sm" variant="secondary" onClick={onDelete} className="w-8 px-0" aria-label={`${conn.name} 삭제`}>
            <Trash2 size={14} aria-hidden="true" />
          </Button>
        </div>
      </div>

      {/*
        실패·주의 사유는 행 안 한 줄 띠로 둔다. 상태와 시각은 위아래 줄이 이미 보여 준다.
        해결 안내(`hint`)는 서버가 점검 때마다 만들고 행에 남기지 않으므로, 방금 저장한 사람에게만 붙는다.
        비밀 키가 없으면 사유도 `자격증명 필요`와 같은 내용이라 쓰지 않는다. 자격증명이 없는 서비스는 사유를 늘 쓴다.
      */}
      {(conn.has_secret || !needsCredential(conn.provider))
        && conn.last_status && conn.last_status !== "ok" && conn.last_detail && (
        <div className={cn(
          "mt-3 flex gap-2 rounded-control px-3 py-2",
          conn.last_status === "fail" ? "bg-bad-muted text-bad-text" : "bg-warn-muted text-warn-text",
        )}>
          <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-footnote font-semibold">{conn.last_detail}</p>
            {hint && <p className="mt-0.5 text-caption text-label-2">{hint}</p>}
          </div>
        </div>
      )}

      {/* 시각 세 가지를 한 줄에 모은다. 가장 자주 바뀌는 점검 시각이 맨 앞이다. */}
      <p className="mt-3 text-caption text-label-3">
        {[
          conn.last_checked_at ? `${relativeTime(conn.last_checked_at)} 점검` : null,
          conn.secret_set_at && `자격증명 ${relativeTime(conn.secret_set_at)} 등록`,
          conn.updated_at && `설정 ${relativeTime(conn.updated_at)} 변경`,
        ].filter(Boolean).join(" · ")}
      </p>
    </li>
  );
}

/** 연결 창 안 입력 묶음의 머리. 창 제목 아래 단계라 작은 회색 글자로 둔다. */
function FormGroupTitle({ children }: { children: ReactNode }) {
  return <p className="pt-2 text-footnote font-semibold text-label-2">{children}</p>;
}

/** 비워 두어도 되는 입력란의 라벨 옆 표시. 거절 사유 입력의 `선택`과 같은 모양이다. */
const OPTIONAL_BADGE =
  "ml-1.5 font-normal text-label-3";

/** 이번 저장에 꼭 넣어야 하는 입력란의 라벨 옆 표시. 주의색으로 `선택`과 구별한다. */
const REQUIRED_BADGE =
  "ml-1.5 font-normal text-warn-text";

/**
 * 쓰기 전용 비밀 값 입력. 붙여 넣은 값을 확인하도록 잠깐 보이게 하는 토글이 붙는다.
 *
 * 토글이 없으면 틀린 키를 저장하고 점검에서야 알게 되는데, 그때는 다시 볼 수 없어 무엇이 틀렸는지 알 수 없다.
 * 토글은 버튼이고 aria 라벨이 있으며 입력의 포커스를 빼앗지 않는다(참고: uxpatterns.dev 비밀번호 패턴).
 * 붙여넣기는 막지 않는다. 자격증명은 대개 비밀번호 관리자나 콘솔에서 복사해 온다.
 */
function SecretInput({ id, label, value, onChange, keep, optional, required, hint }: {
  id: string;
  label: string;
  /** 비어 있을 때 보이는 콘솔 표기(`Access Key ID`). 벤더 콘솔에서 복사해 오는 값이라 이름을 맞춘다. */
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  /** 이미 저장된 값이 있으면 true. 입력란 안내 문구로 알린다. */
  keep: boolean;
  optional?: boolean;
  /** 이번 저장에 다시 넣어야 하면 true. `선택` 대신 `필수`를 붙이고 저장된 값 표시를 지운다. */
  required?: boolean;
}) {
  const [show, setShow] = useState(false);
  return (
    <div>
      <Label htmlFor={id}>
        {label}
        {required
          ? <span className={REQUIRED_BADGE}>필수</span>
          : optional && <span className={OPTIONAL_BADGE}>선택</span>}
      </Label>
      <div className="relative">
        <Input id={id} type={show ? "text" : "password"} value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="off" spellCheck={false} className="pr-10"
          aria-required={required || undefined}
          placeholder={keep && !required ? "●●●●" : hint ?? ""} />
        <button
          type="button"
          onClick={() => setShow((v) => !v)}
          aria-label={show ? `${label} 가리기` : `${label} 보기`}
          aria-pressed={show}
          className="absolute inset-y-0 right-0 my-px mr-px grid w-9 place-items-center rounded-field text-label-4 transition-colors hover:text-label-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
    </div>
  );
}

/**
 * 연결 추가·편집 창을 그린다. 추가와 편집이 같은 폼을 쓰고, 편집에서는 이름과 프로바이더를 읽기 전용으로 보여 준다.
 * 시크릿은 입력했을 때만 보낸다.
 */
function ConnectionForm({
  conn, onClose, onSaved,
}: {
  conn: ConnectionDTO | null;
  onClose: () => void;
  onSaved: (saved: ConnectionSaved) => void;
}) {
  const { toast } = useToast();
  const [name, setName] = useState(conn?.name ?? "");
  const [provider, setProvider] = useState<ConnectionProvider>(conn?.provider ?? DEFAULT_PROVIDER);
  const [config, setConfig] = useState<Record<string, string>>(
    () => Object.fromEntries(Object.entries(conn?.config ?? {}).map(([k, v]) => [
      k, Array.isArray(v) ? bucketListText(v) : String(v ?? ""),
    ])),
  );
  const [accessKey, setAccessKey] = useState("");
  const [secretKey, setSecretKey] = useState("");

  const fields = PROVIDER_FIELDS[provider];
  const cred = CREDENTIAL[provider];
  /*
    요청이 나가는 곳(주소·엔드포인트·리전)을 고치면 저장된 키가 새 주소로 나가므로 키를 다시 받는다.
    서버도 같은 판정으로 거절한다(`secret_required_for_address_change`). 저장을 누르기 전에 키 입력란을 필수로 바꿔 알린다.
  */
  const needSecret = !!conn && secretRequired({
    provider,
    hasSecret: conn.has_secret,
    secretSent: !!accessKey || (cred.kind === "pair" && !!secretKey),
    clearSecret: false,
    before: conn.config,
    after: configBody(provider, config),
  });
  /*
    프로젝트가 쓰는 저장소를 허용 범위에서 빼려다 거절된 경우(409 `allowed_bucket_in_use`).
    먼저 할 일을 창 안에 남긴다. 토스트는 사라지므로 허용 저장소 입력란과 함께 보이게 둔다.
  */
  const [inUse, setInUse] = useState<(ErrorText & { projects: string | null }) | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { config: configBody(provider, config) };
      // 시크릿은 입력했을 때만 보낸다. 빈 입력란은 "바꾸지 않음"이다(편집할 때 기존 값 유지).
      if (accessKey) body.access_key = accessKey;
      if (secretKey && cred.kind === "pair") body.secret_key = secretKey;
      if (conn) return updateConnection(conn.id, body);
      return createConnection({ ...body, name, provider });
    },
    onMutate: () => setInUse(null),
    /*
      저장은 끝났고 점검 결과만 다를 수 있다. 정상이 아니면 저장 결과(굵게)와 점검 사유(작게)를 나눠 알린다.
      사유와 고치는 방법은 목록 행의 띠에도 남는다.
    */
    onSuccess: (saved) => {
      onSaved(saved);
      const done = conn ? "연결을 수정했습니다" : "연결을 추가했습니다";
      const check = analysisOffText(saved.check);
      if (check.status === "ok") toast("success", done);
      else toast("warning", done, `점검 ${HEALTH_LABEL[check.status]} · ${check.detail}`);
    },
    // 오류 사전이 사유를 구분한다.
    // `name_exists`는 사전에 있고, 422 검증 실패도 필드별 사유를 보여 준다.
    onError: (e: unknown) => {
      const t = apiErrorText(e, "저장 실패");
      if ((e as { detail?: unknown } | null)?.detail === "allowed_bucket_in_use") {
        const projects = inUseProjects(e);
        setInUse({ ...t, projects: projects ? inUseProjectsLine(projects) : null });
        return;
      }
      toast("error", t.message, t.hint);
    },
  });

  /*
    두 키가 모두 필요한 서비스(Bedrock)는 저장된 키가 없으면 두 키를 다 받아야 저장된다. 한쪽만 입력해도 막는다.
    서버도 같은 판정으로 거절한다(`bedrock_keys_required`).
  */
  const keysStored = !!conn?.has_secret;
  const typedBoth = !!accessKey.trim() && !!secretKey.trim();
  const typedOne = !!accessKey.trim() !== !!secretKey.trim();
  const needKeys = !!cred.required && (typedOne || (!keysStored && !typedBoth));
  const keyRequired = needSecret || (!!cred.required && (!keysStored || typedOne));
  const valid = name.trim() !== "" && !needSecret && !needKeys;
  // 생성 창의 제출 버튼은 `추가`, 편집 창은 `저장`이다.
  const verb = conn ? "저장" : "추가";
  return (
    <Modal
      open
      onClose={onClose}
      title={conn ? "연결 편집" : "연결 추가"}
      /*
        입력란이 열 개 가깝고 2열 그리드(키, 시크릿)도 있어서,
        448px 폭에서는 두 입력란이 각각 약 190px이 된다.
        그래서 큰 크기를 쓴다.
      */
      size="lg"
      footer={
        <>
          <Button size="sm" variant="secondary" onClick={onClose}>취소</Button>
          <Button size="sm" onClick={() => save.mutate()} disabled={!valid || save.isPending}>
            {save.isPending ? `${verb} 중…` : verb}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <Label htmlFor="c-name">이름</Label>
          <Input id="c-name" value={name} onChange={(e) => setName(e.target.value)}
            placeholder={nameExample(provider)} disabled={!!conn} />
        </div>
        {conn ? (
          /*
            편집 중인 연결이 어떤 서비스인지 창 안에서 보여 준다.
            화면은 프로바이더를 `서비스`라고 부른다. `프로바이더`는 영어를 옮긴 말이다.
          */
          <div>
            <Label htmlFor="c-provider">서비스</Label>
            <Input id="c-provider" value={PROVIDER_LABEL[conn.provider]} disabled readOnly />
          </div>
        ) : (
          <div>
            <Label htmlFor="c-provider">서비스</Label>
            <Select id="c-provider" value={provider}
              onChange={(e) => {
                /*
                  프로바이더를 바꾸면 새 프로바이더가 쓰지 않는 값은 버린다.
                  서버는 `config`를 그대로 저장하므로 서버에서도 걸러지지 않는다.
                  버리지 않으면 앞 프로바이더의 값(엔드포인트·저장소·리전)이 화면에 보이지 않은 채 DB에 들어간다.
                */
                const next = e.target.value as ConnectionProvider;
                const keep = new Set(PROVIDER_FIELDS[next].map((f) => f.key));
                setProvider(next);
                setConfig((c) => Object.fromEntries(
                  Object.entries(c).filter(([k]) => keep.has(k))));
              }}>
              {PROVIDER_GROUPS.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.providers.map((p) => {
                    const note = PROVIDER_NOTE[p];
                    return (
                      <option key={p} value={p}>
                        {note ? `${PROVIDER_LABEL[p]} · ${note}` : PROVIDER_LABEL[p]}
                      </option>
                    );
                  })}
                </optgroup>
              ))}
            </Select>
            {/* 만든 뒤 바꿀 수 없다는 규칙은 고르는 입력란 바로 아래에 둔다. 쓰임새는 서비스 이름 옆(`PROVIDER_NOTE`)이 알린다. */}
            <p className="mt-1 text-caption text-label-3">이름과 서비스는 만든 뒤 바꿀 수 없습니다.</p>
          </div>
        )}

        {/*
          입력란을 `설정값`과 `자격증명` 두 묶음으로 나눈다. 비밀이 아닌 값과 쓰기 전용 값이 한 줄로
          이어져 있으면 어디까지가 다시 볼 수 있는 값인지 알기 어렵다.
        */}
        {fields.length > 0 && <FormGroupTitle>설정값</FormGroupTitle>}
        <div className={cn("grid gap-3", fields.length > 1 && "sm:grid-cols-2")}>
        {fields.map((f) => {
          const set = (v: string) => setConfig((c) => ({ ...c, [f.key]: v }));
          const value = config[f.key] ?? "";
          const label = (
            <Label htmlFor={`c-${f.key}`}>
              {f.label}
              {f.optional && <span className={OPTIONAL_BADGE}>선택</span>}
            </Label>
          );
          if (f.list)
            return (
              <div key={f.key} className="sm:col-span-2">
                {label}
                <Input id={`c-${f.key}`} value={value} onChange={(e) => set(e.target.value)}
                  placeholder={f.example} autoComplete="off" spellCheck={false} />
              </div>
            );
          return (
            <div key={f.key}>
              {label}
              <Input id={`c-${f.key}`} value={value} onChange={(e) => set(e.target.value)}
                placeholder={f.example} />
            </div>
          );
        })}
        </div>
        {/* 결과(굵게), 막는 프로젝트와 먼저 할 일(작게)을 줄로 나눈다. 허용 저장소 입력란 바로 아래라 무엇을 되돌릴지 함께 보인다. */}
        {inUse && (
          <div role="alert" className="flex gap-1.5 text-footnote text-bad-text">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0">
              <p className="font-semibold">{inUse.message}</p>
              {inUse.projects && <p className="text-caption text-label-2">{inUse.projects}</p>}
              {inUse.hint && <p className="text-caption text-label-2">{inUse.hint}</p>}
            </div>
          </div>
        )}

        {cred.kind !== "none" && <FormGroupTitle>자격증명</FormGroupTitle>}
        {/* 시크릿 입력. 쓰기 전용이고, 편집할 때 비워 두면 기존 값을 그대로 둔다. */}
        {cred.kind === "pair" && (
          <div className="grid grid-cols-2 gap-3">
            {/*
              라벨은 한국어로 쓴다. 화면 문구의 기준은 한국어다.
              다만 벤더 콘솔에서 복사해 오는 값이라 콘솔
              표기(`Access Key ID`·`Secret Key`)를 아래 설명에 남긴다.
              이름이 너무 다르면 어느 입력란에 무엇을 넣을지 헷갈린다.
            */}
            <SecretInput id="c-access" label="액세스 키" hint="Access Key ID" value={accessKey} onChange={setAccessKey}
              keep={!!conn?.has_secret} optional={cred.optional} required={keyRequired} />
            <SecretInput id="c-secret" label="비밀 키" hint="Secret Key" value={secretKey} onChange={setSecretKey}
              keep={!!conn?.has_secret} optional={cred.optional} required={keyRequired} />
          </div>
        )}
        {cred.kind === "api_key" && (
          <SecretInput id="c-access" label="API 키" value={accessKey} onChange={setAccessKey}
            keep={!!conn?.has_secret} optional={cred.optional} required={needSecret} />
        )}
        {/*
          안내는 늘 보이는 문구로 둔다. 콘솔 표기(`Access Key ID`)는 빈 입력란에 보이는 이름이라 안내 역할을 하지 못한다.
          저장 방식은 입력 방법과 줄을 나눈다.
        */}
        {/*
          주소를 고쳐 키가 필요해진 이유를 키 입력란 바로 아래에 둔다. 결과(굵게)와 이유(작게)를 나눈다.
          저장 버튼이 막힌 이유도 이 문구가 알린다.
        */}
        {needSecret && (
          <div role="status" className="flex gap-1.5 text-footnote text-warn-text">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0">
              <p className="font-semibold">주소를 바꾸면 키를 다시 입력해야 합니다</p>
              <p className="text-caption text-label-2">저장된 키가 새 주소로 나가지 않도록 주소와 키를 함께 저장합니다.</p>
            </div>
          </div>
        )}
        {/* 키 없이 저장할 수 없는 이유를 키 입력란 바로 아래에 둔다. 결과(굵게)와 이유(작게)를 나눈다. 저장 버튼이 막힌 이유도 이 문구가 알린다. */}
        {needKeys && !needSecret && (
          <div role="status" className="flex gap-1.5 text-footnote text-warn-text">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0">
              <p className="font-semibold">액세스 키와 비밀 키를 모두 입력해야 저장됩니다</p>
              <p className="text-caption text-label-2">키 없는 Bedrock 연결은 서버의 AWS 자격증명을 쓰게 되어 저장하지 않습니다.</p>
            </div>
          </div>
        )}
        {cred.kind !== "none" && (
          <div className="space-y-0.5 text-caption text-label-3">
            {cred.optional && !needSecret && <p>키 없이 접속하는 환경이면 비워 둡니다.</p>}
            {conn?.has_secret && !needSecret && <p>비워 두면 지금 자격증명을 그대로 둡니다.</p>}
            <p>키는 암호화해 저장하고, 저장한 뒤에는 다시 보여 주지 않습니다.</p>
          </div>
        )}
      </div>
    </Modal>
  );
}
