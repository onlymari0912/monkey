from fastapi import APIRouter

# Polaris 클라이언트는 일부 요청에서 "local" 서비스 URL을 찾는다.
# services.get 응답에 local 항목이 필요하므로 이 파일을 지우면 안 된다.
router = APIRouter(prefix="/local", tags=["local"])
