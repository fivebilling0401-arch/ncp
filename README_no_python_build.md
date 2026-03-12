# NCP Billing Monthly Collector - no local Python build

이 패키지는 **회사 PC에 Python 설치 없이** Windows `.exe`를 만드는 방법까지 포함합니다.

## 방법 A. 가장 쉬움: GitHub Actions로 exe 만들기

로컬 PC에는 Python이 없어도 됩니다.

1. 이 폴더 전체를 GitHub 새 저장소에 업로드
2. GitHub 저장소에서 **Actions** 탭 열기
3. 워크플로우 `Build Windows EXE` 실행
4. 완료 후 **Artifacts**에서 `NCP_Billing_Monthly_Collector-windows` 다운로드
5. 압축 해제 후 `NCP_Billing_Monthly_Collector.exe` 실행

### 포함 파일
- `.github/workflows/build-windows-exe.yml`
- `ncp_billing_monthly_gui.py`
- `requirements.txt`

## 방법 B. Python 있는 다른 PC에서 exe만 빌드

개인 PC나 테스트 PC에서 exe를 만들고, exe 파일만 회사 PC로 복사합니다.

## 실행 파일 사용법

1. `memberNo.csv` 준비
   - 헤더: `memberNo`
2. exe 실행
3. 아래 입력
   - Billing API Base URL
   - Access Key
   - Secret Key
   - 조회월 (예: `202602`)
   - memberNo 파일 경로
   - 저장 폴더
4. 결과 확인
   - `demand_merged_YYYYMM.csv`
   - `product_demand_merged_YYYYMM.csv`
   - `summary.json`

## memberNo CSV 예시

```csv
memberNo
1234567
2345678
3456789
```

## 주의

- 이 대화 환경에서는 Windows 바이너리 exe를 직접 빌드해 전달할 수 없습니다.
- 대신 **GitHub Actions로 로컬 Python 없이 exe를 만들 수 있게** 워크플로우를 넣었습니다.
- 사내망에서 GitHub 사용이 막혀 있으면, Python 있는 다른 Windows PC에서 exe를 1회 빌드한 뒤 exe만 전달하는 방식이 필요합니다.
