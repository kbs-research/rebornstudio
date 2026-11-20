"""QC 어댑터 계약 테스트. 등록된 모든 어댑터가 이 테스트 묶음을 통과해야 한다.

새 어댑터(로컬 LLM·GPT·Claude)를 더할 때 이 파일은 고치지 않는다.
`all_adapters()`에 등록하면 자동으로 같은 검사를 받는다.
그래야 모델을 바꿔도 같은 데이터가 같게 판정된다.
"""
import pytest
from api_gateway.services.caption_qc import adapters, rules
from reborn_contracts.analysis import Capability

#: 모델을 부르는 어댑터가 쓰는 접속값. 스텁이 받으므로 실제 요청은 나가지 않는다.
_CFG = adapters.ModelConfig(model="test-model", base_url="http://stub", api_key="")

_SENTENCES = [
    {"id": "s1", "text": "앵커가 스튜디오에서 오늘의 주요 뉴스를 전합니다."},
    {"id": "s2", "text": "화면 하단에 자막이 표시되어 있습니다."},
]


async def _stub_client(cfg, system, user):
    """네트워크 없이 계약을 검사하도록 모델 대신 응답한다.

    모델을 부르는 어댑터도 같은 계약을 지켜야 하지만 실호출은 비용이 들고 결과도 결정적이지 않다.
    그래서 어댑터는 `client`를 주입받는다. 주입을 받는 것 자체가 계약의 일부다.
    """
    return '{"findings":[{"sentence_id":"s1","check":"spelling","message":"오탈자"}]}'


def _all():
    """계약 검사 대상을 만든다. 모델을 부르는 어댑터는 스텁을 주입한 사본으로 돌린다.

    카탈로그의 인스턴스를 그대로 쓰면 실호출이 나가거나 설정 없음 오류로 실패한다.
    새 어댑터를 더해도 이 함수는 고치지 않는다. `client` 주입 규약만 지키면 된다.
    """
    out = []
    for ad in adapters.all_adapters():
        try:
            out.append(type(ad)(client=_stub_client))
        except TypeError:      # 주입을 받지 않는 어댑터(더미)는 그대로 쓴다
            out.append(ad)
    return out


def test_registry_is_not_empty():
    """어댑터 목록이 비지 않는지 확인한다. 더미도 정식 어댑터다. 목록이 비면 전 구간을 모델 없이 돌릴 수 없다."""
    assert _all()


@pytest.mark.parametrize("ad", _all(), ids=lambda a: a.key)
def test_declares_caption_qc_capability(ad):
    """캡션 QC 카탈로그의 어댑터가 캡션 QC 능력을 선언하는지 확인한다. 계약 테스트는 선언한 능력만 검사한다."""
    assert Capability.CAPTION_QC in ad.capabilities
    assert ad.key and isinstance(ad.key, str)
    assert adapters.get(ad.key) is not None


@pytest.mark.parametrize("ad", _all(), ids=lambda a: a.key)
async def test_findings_match_storage_shape(ad):
    """지적이 `caption_qc_runs.findings`에 저장되는 모양인지 확인한다. 모양이 다르면 저장이 깨진다."""
    res = await ad.review(adapters.QcRequest(
        sentences=[{"id": "s1", "text": "같은 같은 말이 반복됩니다."}],
        glossary={"티브이": "TV"},
        model_config=_CFG,
    ))
    assert isinstance(res.findings, list)
    for f in res.findings:
        assert set(f) <= {"check", "severity", "message", "sentence_id"}
        assert f["check"] and f["message"]
        assert f["severity"] in ("warn", "error")
        # 문장 지적이면 입력으로 준 id를 그대로 돌려줘야 한다. 만들어 낸 id는 화면에서 찾을 수 없다.
        if "sentence_id" in f:
            assert f["sentence_id"] == "s1"


@pytest.mark.parametrize("ad", _all(), ids=lambda a: a.key)
async def test_deterministic_for_same_input(ad):
    """같은 입력이면 같은 결과를 내는지 확인한다. 결과가 달라지면 재실행할 때마다 보류 여부가 바뀐다.

    실 모델 어댑터는 temperature=0으로 이 계약을 지킨다.
    """
    req = adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG)
    assert (await ad.review(req)).findings == (await ad.review(req)).findings


@pytest.mark.parametrize("ad", _all(), ids=lambda a: a.key)
async def test_empty_and_deleted_inputs_do_not_crash(ad):
    """빈 캡션과 삭제 표시한 문장에서 실패하지 않는지 확인한다.

    빈 값 판정은 규칙(`check_caption`)이 맡는다.
    """
    assert (await ad.review(adapters.QcRequest(sentences=[], model_config=_CFG))).findings == []
    deleted = [{"id": "s1", "text": "지워진 문장입니다.", "deleted": True}]
    assert (await ad.review(
        adapters.QcRequest(sentences=deleted, model_config=_CFG)
    )).findings == []


@pytest.mark.parametrize("ad", _all(), ids=lambda a: a.key)
async def test_prompt_hash_is_none_or_hex(ad):
    """프롬프트 지문이 없거나 sha256인지 확인한다. 지문은 판정을 재구성하는 근거다."""
    res = await ad.review(adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    assert res.prompt_hash is None or (
        len(res.prompt_hash) == 64 and all(c in "0123456789abcdef" for c in res.prompt_hash)
    )


@pytest.mark.parametrize("ad", _all(), ids=lambda a: a.key)
async def test_custom_prompt_changes_the_recorded_hash(ad):
    """프롬프트를 바꾸면 지문도 바뀌는지 확인한다. 지문이 그대로면 보류 이유를 추적할 수 없다."""
    base = await ad.review(adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    custom = await ad.review(adapters.QcRequest(
        sentences=_SENTENCES, model_config=_CFG,
        prompts={"spelling": "맞춤법만 보고 문체는 보지 마세요"},
    ))
    assert base.prompt_hash != custom.prompt_hash
    assert custom.prompt_hash is not None


# 더미 고유 동작(정식 어댑터지만 모델처럼 동작하지 않는다)


async def test_dummy_flags_repeated_words_and_glossary():
    """더미가 반복 낱말과 용어집 위반을 지적하는지 확인한다."""
    ad = adapters.get("dummy")
    res = await ad.review(adapters.QcRequest(
        sentences=[{"id": "s1", "text": "같은 같은 말이 반복됩니다."},
                   {"id": "s2", "text": "티브이 앵커가 전합니다."}],
        glossary={"티브이": "TV"},
    ))
    checks = {f["check"] for f in res.findings}
    assert checks == {"repeated_word", "glossary"}
    # 모델을 부르지 않았으므로 빈 문자열이 아니라 None이다.
    assert res.model is None


async def test_dummy_does_not_pretend_to_judge_context():
    """더미가 맞춤법·부적절 문장 지적을 내지 않는지 확인한다.

    더미가 그런 지적을 내면 실 어댑터를 붙이기 전에 이미 기능이 동작한다고 오해하게 된다.
    """
    res = await adapters.get("dummy").review(adapters.QcRequest(
        sentences=[{"id": "s1", "text": "아무 문제 없는 평범한 문장입니다."}]
    ))
    assert res.findings == []


async def test_adapter_findings_feed_the_shared_verdict():
    """판정을 어댑터가 아니라 `rules`가 도출하는지 확인한다. 모델을 바꿔도 기준이 바뀌지 않는다."""
    res = await adapters.get("dummy").review(adapters.QcRequest(
        sentences=[{"id": "s1", "text": "같은 같은 말입니다."}]
    ))
    assert rules.verdict_of(res.findings) == "needs_review"   # warn만 있다
    assert rules.qc_status_of(rules.verdict_of(res.findings)) == "held"


# LLM 어댑터 고유 동작


def _llm(reply: str):
    """지정한 문자열로 답하는 모델 스텁을 붙인 어댑터를 만든다."""
    async def client(cfg, system, user):
        """정해 둔 답을 돌려준다."""
        return reply
    return adapters.LlmQcAdapter(client=client)


async def test_llm_maps_response_to_findings():
    """모델 응답의 지적을 저장 모양으로 옮기고 판정한 모델 이름을 남기는지 확인한다."""
    res = await _llm(
        '{"findings":[{"sentence_id":"s1","check":"spelling","message":"오탈자: 요금표"}]}'
    ).review(adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    assert res.findings == [
        {"check": "spelling", "severity": "warn", "message": "오탈자: 요금표", "sentence_id": "s1"}
    ]
    assert res.model == "test-model"   # 어느 모델이 판정했는지 남는다


async def test_llm_severity_is_never_error():
    """모델이 error를 내도 심각도를 warn으로 낮추는지 확인한다. 심각도는 모델이 정하지 않는다.

    반려추천은 규칙이 확실한 경우에만 준다. 모델의 판단은 사람이 확인하게 한다.
    """
    res = await _llm(
        '{"findings":[{"sentence_id":"s1","check":"inappropriate",'
        '"message":"부적절","severity":"error"}]}'
    ).review(adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    assert res.findings[0]["severity"] == "warn"


@pytest.mark.parametrize("reply", [
    '{"findings":[{"check":"모르는검사","message":"x"}]}',      # 목록에 없는 검사 이름
    '{"findings":[{"check":"spelling","message":"  "}]}',      # 빈 메시지
])
async def test_llm_discards_untrusted_output(reply):
    """모델 출력 중 읽을 수는 있지만 믿을 수 없는 항목을 버리는지 확인한다.

    검증하지 않은 값이 화면에 뜨면 사람이 시스템의 판정으로 읽는다.
    아예 읽지 못한 응답은 `test_llm_raises_when_the_answer_is_unreadable`가 다룬다.
    두 경우를 모두 빈 목록으로 돌려주면 모델이 무엇을 응답했든 통과가 된다.
    """
    res = await _llm(reply).review(adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    assert res.findings == []


@pytest.mark.parametrize("reply", [
    "이건 JSON이 아닙니다",         # 형식 깨짐
    '{"findings":"목록이 아님"}',    # 모양 다름
])
async def test_llm_raises_when_the_answer_is_unreadable(reply):
    """읽지 못한 응답이 통과가 아니라 예외인지 확인한다. 호출부가 예외를 받아 `rules`로 기록한다.

    빈 목록으로 돌려주면 파서가 깨진 날 모든 건이 조용히 `pass`가 된다.
    코드펜스 한 겹만 있어도 지적 여러 건이 0건 통과로 기록될 수 있다.
    """
    with pytest.raises(adapters.QcUnparseableError):
        await _llm(reply).review(adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))


async def test_llm_drops_hallucinated_sentence_id():
    """입력에 없는 문장 id를 떼는지 확인한다.

    화면이 찾지 못하는 id를 달면 지적을 어느 문장에도 붙일 수 없다.
    """
    res = await _llm(
        '{"findings":[{"sentence_id":"없는id","check":"tone","message":"구어체"}]}'
    ).review(adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    assert res.findings and "sentence_id" not in res.findings[0]


async def test_llm_without_config_raises_not_configured():
    """설정이 없으면 통과가 아니라 예외인지 확인한다.

    빈 결과를 주면 캡션에 문제가 없는 경우와 모델을 부르지 못한 경우를 구분할 수 없다.
    호출부는 이 예외를 받아 `pending`으로 남기고 재시도한다.
    """
    with pytest.raises(adapters.QcNotConfiguredError):
        await _llm("{}").review(adapters.QcRequest(sentences=_SENTENCES))


async def test_llm_skips_model_call_when_nothing_to_review():
    """빈 캡션에는 모델을 부르지 않는지 확인한다. 호출에는 비용이 들고 빈 값 판정은 규칙이 맡는다."""
    called = False

    async def client(cfg, system, user):
        """호출됐다는 사실만 기록한다."""
        nonlocal called
        called = True
        return "{}"

    res = await adapters.LlmQcAdapter(client=client).review(
        adapters.QcRequest(sentences=[], model_config=_CFG)
    )
    assert res.findings == [] and called is False


# ── Bedrock (Converse) ────────────────────────────────────────────────────────


async def test_bedrock_shares_the_same_translation():
    """Bedrock도 같은 번역부를 쓰는지 확인한다. 검증이 공급자마다 다르면 고칠 때 한쪽만 고쳐진다.

    호출 방법만 다르고 프롬프트·파싱·심각도 고정은 공통(`_PromptQcAdapter`)이다.
    """
    async def client(cfg, system, user):
        """리전 전달을 확인하고 error 심각도의 지적을 돌려준다."""
        assert cfg.region == "us-east-1"          # 리전이 실제로 전달된다
        return ('{"findings":[{"sentence_id":"s1","check":"tone",'
                '"message":"구어체","severity":"error"}]}')

    res = await adapters.BedrockQcAdapter(client=client).review(adapters.QcRequest(
        sentences=_SENTENCES,
        model_config=adapters.ModelConfig(
            model="anthropic.claude-sonnet-4-20250514-v1:0", region="us-east-1",
            api_key="AKIA_TEST", api_secret="secret",
        ),
    ))
    # 모델이 error를 내도 warn이다. 공통 번역부를 쓰므로 자동으로 지켜진다.
    assert res.findings[0]["severity"] == "warn"
    assert res.model.startswith("anthropic.claude")


async def test_bedrock_without_region_is_not_configured():
    """리전이 없으면 통과가 아니라 예외인지 확인한다. Bedrock은 리전에 따라 쓸 수 있는 모델이 다르다."""
    with pytest.raises(adapters.QcNotConfiguredError):
        await adapters._bedrock_call(
            adapters.ModelConfig(model="anthropic.claude-x", region=None), "sys", "user"
        )


# 모델 응답 읽기: 실 모델이 돌려주는 형식들


_FOUR = {"m1", "m2", "m3", "m4"}


@pytest.mark.parametrize("raw,expect", [
    # 코드펜스 한 겹 때문에 지적 여러 건이 0건 통과가 되면 안 된다.
    ('```json\n{"findings":[{"sentence_id":"m2","check":"spelling","message":"취제 → 취재"}]}\n```', 1),
    # 언어 태그 없는 펜스도 흔하다.
    ('```\n{"findings":[{"sentence_id":"m2","check":"spelling","message":"오타"}]}\n```', 1),
    # 앞뒤로 한 줄씩 붙이는 모델도 있다. 첫 `{`부터 마지막 `}`까지 읽는다.
    ('검토했습니다.\n{"findings":[{"sentence_id":"m4","check":"tone","message":"종결"}]}\n이상.', 1),
    # 순수 JSON도 읽는다.
    ('{"findings":[{"sentence_id":"m1","check":"glossary","message":"용어"}]}', 1),
    # 지적이 없는 응답은 예외 없이 빈 목록으로 읽는다.
    ('{"findings":[]}', 0),
])
def test_model_answers_are_read_through_the_wrapping(raw, expect):
    """코드펜스 등으로 감싼 모델 응답에서 JSON을 읽는지 확인한다. 프롬프트가 요구해도 모델은 코드펜스를 두른다."""
    assert len(adapters._parse_findings(raw, _FOUR)) == expect


@pytest.mark.parametrize("raw", ["", "   ", "죄송합니다, 판단할 수 없습니다.", "not json at all",
                                 '{"result":"ok"}'])
def test_unreadable_answer_is_not_a_pass(raw):
    """읽지 못한 응답을 빈 지적 목록으로 돌려주지 않는지 확인한다.

    빈 목록으로 돌려주면 파서가 깨진 날 모든 건이 조용히 `pass`로 기록되고, 모델이 무엇을 응답했든 통과가 된다.
    하지 않은 검사를 한 것처럼 기록하지 않는다.
    """
    with pytest.raises(adapters.QcUnparseableError):
        adapters._parse_findings(raw, _FOUR)


# Bedrock: 실 계정에서 확인한 동작


class _FakeClientError(Exception):
    """botocore `ClientError` 대역. 판별 기준인 메시지만 확인한다."""


def _fake_bedrock(monkeypatch, fail_on_temperature: bool, calls: list):
    """`boto3.client("bedrock-runtime")` 대역을 심는다."""
    import botocore.exceptions

    class _Client:
        """`temperature`를 거부할 수 있는 bedrock-runtime 대역."""
        def converse(self, **kw):
            """호출 설정을 기록하고, 설정에 따라 `temperature`를 거부한다."""
            calls.append(kw["inferenceConfig"])
            if fail_on_temperature and "temperature" in kw["inferenceConfig"]:
                raise botocore.exceptions.ClientError(
                    {"Error": {"Code": "ValidationException",
                               "Message": "`temperature` is deprecated for this model."}},
                    "Converse",
                )
            return {"output": {"message": {"content": [{"text": '{"findings":[]}'}]}}}

    monkeypatch.setattr("boto3.client", lambda *a, **k: _Client())


def test_bedrock_retries_without_temperature_when_the_model_rejects_it(monkeypatch):
    """모델이 `temperature`를 거부하면 빼고 다시 부르는지 확인한다. 일부 새 모델은 `temperature`를 받지 않는다.

    모델 목록을 코드에 고정하면 새 모델이 나올 때마다 낡는다.
    그래서 한 번 거부한 모델을 기억해 두고 다음부터 `temperature`를 빼고 부른다.
    `us.anthropic.claude-sonnet-5`가 이렇게 거부한다.
    """
    adapters._NO_TEMPERATURE.discard("m-reject")
    calls: list = []
    _fake_bedrock(monkeypatch, fail_on_temperature=True, calls=calls)
    cfg = adapters.ModelConfig(model="m-reject", region="us-west-2",
                                 api_key="AKIA_TEST", api_secret="secret")

    adapters._bedrock_call(cfg, "sys", "user")
    assert "temperature" in calls[0] and "temperature" not in calls[1]   # 빼고 다시 불렀다

    # 두 번째 호출부터는 처음부터 넣지 않는다. 매번 한 번씩 실패하지 않게 한다.
    calls.clear()
    adapters._bedrock_call(cfg, "sys", "user")
    assert len(calls) == 1 and "temperature" not in calls[0]
    adapters._NO_TEMPERATURE.discard("m-reject")


def test_bedrock_does_not_retry_other_failures(monkeypatch):
    """`temperature` 외의 실패는 재시도하지 않고 그대로 올리는지 확인한다. 넓게 잡으면 실제 실패가 가려진다."""
    import botocore.exceptions

    class _Client:
        """권한 오류만 내는 bedrock-runtime 대역."""
        def converse(self, **kw):
            """권한 오류를 낸다."""
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}}, "Converse")

    monkeypatch.setattr("boto3.client", lambda *a, **k: _Client())
    with pytest.raises(botocore.exceptions.ClientError):
        adapters._bedrock_call(
            adapters.ModelConfig(model="m-denied", region="us-west-2",
                                 api_key="AKIA_TEST", api_secret="secret"), "sys", "user")


def test_default_prompt_states_the_ending_rule():
    """기본 프롬프트의 `tone` 항목이 판단 기준을 명시하는지 확인한다.

    기준이 없으면 모델이 자기 기준을 만든다.
    "종결이 어색하거나"처럼 쓰면 모델이 반말 종결을 놓치고 규칙이 아닌 과거형을 대신 지적한다.
    실제 캡션은 대부분 `합니다`체라서 `합니다`체를 기준으로 둔다.
    프롬프트는 문자열이라 조용히 지워질 수 있으므로 이 테스트가 그 문구를 지킨다.
    """
    p = adapters._SYSTEM_PROMPT
    assert "~합니다" in p, "종결 정본이 프롬프트에서 사라졌다"
    assert "인용" in p, "인용 예외가 사라지면 발언 인용이 전부 지적된다"
    assert "시제는 규칙이 아닙니다" in p, "이 줄이 없으면 모델이 과거형을 지적한다(실측)"


# 일시 오류에 한 번만 다시 부른다


async def test_transient_failure_is_retried_once(monkeypatch):
    """429가 한 번 나면 다시 부르는지 확인한다. 일시 오류 한 번 때문에 모델 검수를 받지 못하게 두지 않는다."""
    import httpx

    calls = []

    async def flaky(cfg, system, user):
        """처음 한 번만 429를 내고 그다음에는 빈 지적을 돌려준다."""
        calls.append(1)
        if len(calls) == 1:
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("POST", "http://x"),
                response=httpx.Response(429),
            )
        return '{"findings":[]}'

    monkeypatch.setattr(adapters, "_RETRY_ONCE_AFTER", 0)
    res = await adapters.LlmQcAdapter(client=flaky).review(
        adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    assert len(calls) == 2, "다시 안 불렀다"
    assert res.findings == []


async def test_permanent_failure_is_not_retried(monkeypatch):
    """403은 다시 불러도 같으므로 재시도하지 않는지 확인한다. 넓게 잡으면 실제 실패가 재시도에 가려진다."""
    import httpx

    calls = []

    async def denied(cfg, system, user):
        """늘 403을 낸다."""
        calls.append(1)
        raise httpx.HTTPStatusError(
            "403", request=httpx.Request("POST", "http://x"), response=httpx.Response(403))

    monkeypatch.setattr(adapters, "_RETRY_ONCE_AFTER", 0)
    with pytest.raises(httpx.HTTPStatusError):
        await adapters.LlmQcAdapter(client=denied).review(
            adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    assert len(calls) == 1, "영구 실패를 다시 불렀다"


async def test_retry_happens_at_most_once(monkeypatch):
    """두 번째도 실패하면 거기서 끝내는지 확인한다.

    재시도는 요청 안에서 돌므로 검수자를 오래 기다리게 하지 않는다.
    """
    import httpx

    calls = []

    async def always_429(cfg, system, user):
        """늘 429를 낸다."""
        calls.append(1)
        raise httpx.HTTPStatusError(
            "429", request=httpx.Request("POST", "http://x"), response=httpx.Response(429))

    monkeypatch.setattr(adapters, "_RETRY_ONCE_AFTER", 0)
    with pytest.raises(httpx.HTTPStatusError):
        await adapters.LlmQcAdapter(client=always_429).review(
            adapters.QcRequest(sentences=_SENTENCES, model_config=_CFG))
    assert len(calls) == 2, f"호출이 {len(calls)}번 — 한 번만 다시 불러야 한다"
