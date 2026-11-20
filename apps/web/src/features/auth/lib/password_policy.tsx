/**
 * 비밀번호 정책. 서버 규칙을 화면에 보여 주는 단일 구현이다.
 *
 * 서버는 `security.validate_password_policy`(12자+·대소문자·숫자·특수문자)로 검사하고,
 * 어기면 422와 함께 같은 문장을 돌려준다.
 * 화면은 이 규칙을 미리 보여 주어 사용자가 제출 전에 입력하면서 규칙을 확인하게 한다.
 *
 * 비밀번호를 만드는 세 화면(`내 계정`·최초 설정·재설정)이 모두 이 파일을 쓴다.
 * 화면마다 규칙을 적으면 한쪽만 고쳤을 때 같은 규칙을 다르게 보여 준다
 * (판정 규칙은 한 곳에만 둔다).
 *
 * 서버와 이 파일은 언어가 달라 하나의 정의로 묶을 수 없다.
 * 그래서 이 파일은 안내만 하고 최종 판정은 서버에 맡긴다.
 * 화면이 통과시켜도 422가 오면 서버 문장을 그대로 보여 준다.
 */
import { Check } from "lucide-react";

import { cn } from "@/shared/lib/cn";

/** 비밀번호 규칙 다섯 가지. 라벨은 화면에 그대로 나온다. 영문 대소문자는 무엇의 대소문자인지 적는다. */
export const PASSWORD_RULES = [
  { re: /.{12,}/, label: "12자 이상" },
  { re: /[A-Z]/, label: "영문 대문자" },
  { re: /[a-z]/, label: "영문 소문자" },
  { re: /\d/, label: "숫자" },
  { re: /[^A-Za-z0-9]/, label: "특수문자" },
];

/** 규칙 다섯 가지를 모두 만족하면 true. 제출 버튼 활성 여부에 쓴다. */
export function meetsPasswordPolicy(pwd: string): boolean {
  return PASSWORD_RULES.every((r) => r.re.test(pwd));
}

/**
 * 규칙 체크 목록을 그린다. 충족한 규칙은 확인 표시와 ok 글자, 아닌 규칙은 흐린 점이다. 입력이 비면 그리지 않는다.
 *
 * `always`를 주면 입력 전에도 모든 규칙을 그린다. 제출 버튼이 규칙 때문에 꺼져 있는
 * 화면에서 쓴다. 입력 전에 규칙이 보이지 않으면 버튼이 왜 꺼져 있는지 알 수 없다.
 * 충족 여부는 스크린리더에 글자로 읽힌다(`충족`·`미충족`). 색과 모양에만 의존하지 않는다.
 */
export function PasswordRules({ value, always = false }: { value: string; always?: boolean }) {
  if (!value && !always) return null;
  return (
    // 좁은 폭에서는 세 열 격자다. 한 줄로 흐르면 마지막 한 항목만 둘째 줄에 남는다.
    // 넓은 폭의 입력란(약 370px)에 다섯 항목이 한 줄에 들어가도록 간격과 점 여백을 좁게 둔다.
    <ul className="grid grid-cols-3 gap-x-4 gap-y-1 sm:flex sm:flex-wrap sm:gap-x-3">
      {PASSWORD_RULES.map((r) => {
        const ok = r.re.test(value);
        return (
          <li
            key={r.label}
            className={cn("inline-flex items-center gap-1 text-caption", ok ? "text-ok-text" : "text-label-3")}
          >
            {ok ? (
              <Check size={13} strokeWidth={2.5} aria-hidden="true" />
            ) : (
              <span aria-hidden="true" className="mx-0.5 size-1 rounded-capsule bg-label-4" />
            )}
            {r.label}
            <span className="sr-only">{ok ? " 충족" : " 미충족"}</span>
          </li>
        );
      })}
    </ul>
  );
}
