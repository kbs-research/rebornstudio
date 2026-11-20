# 백업 러너 이미지. pg_dump/pg_restore, aws-cli, redis-cli를 담는다.
#
# 셋을 다 가진 공식 이미지가 없어 별도 이미지를 만든다. 실행 시점에 `apk add`로 채우면
# 알파인 저장소가 불안정할 때 백업이 조용히 멈춘다. 그래서 빌드 시점에 고정한다.
FROM postgres:16-alpine

RUN apk add --no-cache aws-cli redis bash

COPY infra/backup/backup.sh infra/backup/restore.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh

# NCP Object Storage는 aws-cli v2.23+가 기본으로 켜는 무결성 체크섬(CRC32)을 거부한다.
# 증상이 `AccessDenied`라서 권한 문제로 오진하기 쉽다. 키가 정상이어도 이렇게 실패한다.
# boto3를 쓰는 앱 코드가 정상인데 이 컨테이너만 막히면 여기부터 의심한다.
ENV AWS_REQUEST_CHECKSUM_CALCULATION=when_required \
    AWS_RESPONSE_CHECKSUM_VALIDATION=when_required

# 이미지의 postgres 사용자(uid·gid 70)로 실행한다. 덤프는 DB 접속과 /tmp 임시 파일, 버킷 업로드만 하므로
# root가 필요 없다. 덤프 임시 폴더는 mktemp가 /tmp에 만든다.
USER postgres

# 기본은 주기 루프다. 호스트 cron을 쓰면 서버에 손으로 걸어야 하는 절차가 하나 늘고,
# 그 절차는 잊힌다. compose가 서비스로 들고 있으면 배포와 함께 따라온다.
ENV BACKUP_INTERVAL_SECONDS=43200
CMD ["bash","-c","while true; do backup.sh || echo '❌ 백업 실패 — 다음 주기에 재시도'; sleep \"$BACKUP_INTERVAL_SECONDS\"; done"]
