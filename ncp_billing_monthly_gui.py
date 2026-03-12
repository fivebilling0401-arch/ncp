import csv
import json
import time
import hmac
import base64
import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode

import requests
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

APP_TITLE = "NCP 월별 빌링 RAW 수집기"
DEFAULT_BASE_URL = "https://billingapi.apigw.ntruss.com/billing/v1"


def make_signature(access_key: str, secret_key: str, method: str, uri_with_query: str, timestamp: str) -> str:
    message = f"{method} {uri_with_query}\n{timestamp}\n{access_key}"
    digest = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def ncp_get(base_url: str, access_key: str, secret_key: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    query = urlencode(params, doseq=True)
    uri_with_query = f"{path}?{query}" if query else path
    url = f"{base_url}{uri_with_query}"
    timestamp = str(int(time.time() * 1000))
    headers = {
        "x-ncp-apigw-timestamp": timestamp,
        "x-ncp-iam-access-key": access_key,
        "x-ncp-apigw-signature-v2": make_signature(access_key, secret_key, "GET", uri_with_query, timestamp),
        "Content-Type": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=60)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"HTTP {resp.status_code} 오류\nURL: {url}\n응답: {resp.text[:2000]}") from e
    return resp.json()


def chunked(seq: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def flatten_dict(obj: Any, prefix: str = "", out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{prefix}.{k}" if prefix else str(k)
            flatten_dict(v, new_key, out)
    elif isinstance(obj, list):
        out[prefix] = json.dumps(obj, ensure_ascii=False)
    else:
        out[prefix] = obj
    return out


def find_first_list_of_dicts(obj: Any, preferred_keys: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    preferred_keys = preferred_keys or []
    if isinstance(obj, dict):
        for key in preferred_keys:
            value = obj.get(key)
            if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                return value
        for v in obj.values():
            result = find_first_list_of_dicts(v, preferred_keys)
            if result:
                return result
    elif isinstance(obj, list) and all(isinstance(x, dict) for x in obj):
        return obj
    return []


def extract_rows(data: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    if kind == "demand":
        preferred = ["demandCostList"]
    else:
        preferred = ["productDemandCostList", "productDemandList", "productList", "demandCostList"]
    items = find_first_list_of_dicts(data, preferred)
    return [flatten_dict(item) for item in items]


def save_json(path: Path, obj: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def save_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def call_paged_cost_api(base_url: str, access_key: str, secret_key: str, path: str, month: str,
                        member_batch: List[str], page_size: int, logger) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    page_no = 1
    raw_pages: List[Dict[str, Any]] = []
    rows_all: List[Dict[str, Any]] = []
    kind = "product" if "Product" in path else "demand"

    while True:
        params: Dict[str, Any] = {
            "startMonth": month,
            "endMonth": month,
            "responseFormatType": "json",
            "isPartner": "true",
            "pageNo": page_no,
            "pageSize": page_size,
            "memberNoList": member_batch,
        }
        data = ncp_get(base_url, access_key, secret_key, path, params)
        raw_pages.append(data)
        rows = extract_rows(data, kind)
        rows_all.extend(rows)

        flat = flatten_dict(data)
        total_count = None
        for key in [
            "getDemandCostListResponse.totalRows",
            "getDemandCostListResponse.totalCount",
            "getProductDemandCostListResponse.totalRows",
            "getProductDemandCostListResponse.totalCount",
            "totalRows",
            "totalCount",
        ]:
            val = flat.get(key)
            if val not in (None, ""):
                try:
                    total_count = int(val)
                    break
                except Exception:
                    pass

        has_next = False
        if total_count is not None:
            has_next = page_no * page_size < total_count
        else:
            has_next = len(rows) >= page_size and len(rows) > 0

        logger(f"  - {path.split('/')[-1]} page={page_no}, rows={len(rows)}, next={has_next}")
        if not has_next:
            break
        page_no += 1
        time.sleep(0.2)

    return raw_pages, rows_all


def read_member_numbers(path_str: str) -> List[str]:
    path = Path(path_str)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
        if rows and "memberNo" in rows[0]:
            return [str(r["memberNo"]).strip() for r in rows if str(r.get("memberNo", "")).strip()]
    candidates = []
    for token in text.replace("\n", ",").replace("\r", ",").split(","):
        token = token.strip()
        if token:
            candidates.append(token)
    return candidates


def collect_monthly(base_url: str, access_key: str, secret_key: str, month: str, member_file: str,
                    output_dir: str, member_batch_size: int, page_size: int, logger) -> Path:
    members = read_member_numbers(member_file)
    if not members:
        raise ValueError("memberNo 파일에서 회원번호를 찾지 못했습니다.")
    out_dir = Path(output_dir).expanduser().resolve() / f"ncp_billing_{month}"
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_demand: List[Dict[str, Any]] = []
    merged_product: List[Dict[str, Any]] = []

    logger(f"총 회원번호 수: {len(members)}")
    for idx, member_batch in enumerate(chunked(members, member_batch_size), start=1):
        logger(f"배치 {idx} 시작 (건수: {len(member_batch)})")

        demand_raw, demand_rows = call_paged_cost_api(
            base_url, access_key, secret_key,
            "/cost/getDemandCostList", month, member_batch, page_size, logger
        )
        save_json(out_dir / f"demand_{month}_batch{idx}.json", demand_raw)
        save_csv(out_dir / f"demand_{month}_batch{idx}.csv", demand_rows)
        merged_demand.extend(demand_rows)

        product_raw, product_rows = call_paged_cost_api(
            base_url, access_key, secret_key,
            "/cost/getProductDemandCostList", month, member_batch, page_size, logger
        )
        save_json(out_dir / f"product_demand_{month}_batch{idx}.json", product_raw)
        save_csv(out_dir / f"product_demand_{month}_batch{idx}.csv", product_rows)
        merged_product.extend(product_rows)

        time.sleep(0.3)

    save_csv(out_dir / f"demand_merged_{month}.csv", merged_demand)
    save_csv(out_dir / f"product_demand_merged_{month}.csv", merged_product)

    summary = {
        "month": month,
        "member_count": len(members),
        "demand_rows": len(merged_demand),
        "product_rows": len(merged_product),
        "output_dir": str(out_dir),
    }
    save_json(out_dir / "summary.json", summary)
    return out_dir


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("860x730")
        self.resizable(True, True)
        self._build()

    def _build(self):
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        self.vars = {
            "base_url": tk.StringVar(value=DEFAULT_BASE_URL),
            "access_key": tk.StringVar(),
            "secret_key": tk.StringVar(),
            "month": tk.StringVar(),
            "member_file": tk.StringVar(),
            "output_dir": tk.StringVar(value=str(Path.home() / "Desktop")),
            "member_batch_size": tk.StringVar(value="200"),
            "page_size": tk.StringVar(value="1000"),
        }

        row = 0
        for label, key, show in [
            ("Billing API Base URL", "base_url", None),
            ("Access Key", "access_key", None),
            ("Secret Key", "secret_key", "*"),
            ("조회 월 (YYYYMM)", "month", None),
        ]:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(frame, textvariable=self.vars[key], width=78, show=show).grid(row=row, column=1, sticky="ew", pady=4)
            row += 1

        ttk.Label(frame, text="memberNo 파일").grid(row=row, column=0, sticky="w", pady=4)
        member_row = ttk.Frame(frame)
        member_row.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Entry(member_row, textvariable=self.vars["member_file"], width=62).pack(side="left", fill="x", expand=True)
        ttk.Button(member_row, text="찾아보기", command=self.pick_member_file).pack(side="left", padx=(8, 0))
        row += 1

        ttk.Label(frame, text="저장 폴더").grid(row=row, column=0, sticky="w", pady=4)
        out_row = ttk.Frame(frame)
        out_row.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Entry(out_row, textvariable=self.vars["output_dir"], width=62).pack(side="left", fill="x", expand=True)
        ttk.Button(out_row, text="선택", command=self.pick_output_dir).pack(side="left", padx=(8, 0))
        row += 1

        ttk.Label(frame, text="member 배치 크기").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.vars["member_batch_size"], width=20).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        ttk.Label(frame, text="pageSize").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.vars["page_size"], width=20).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 8))
        self.run_btn = ttk.Button(btn_row, text="수집 시작", command=self.start_job)
        self.run_btn.pack(side="left")
        ttk.Button(btn_row, text="입력 초기화", command=self.clear_inputs).pack(side="left", padx=8)
        row += 1

        ttk.Label(frame, text="실행 로그").grid(row=row, column=0, sticky="nw", pady=4)
        self.log = scrolledtext.ScrolledText(frame, height=24)
        self.log.grid(row=row, column=1, sticky="nsew", pady=4)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(row, weight=1)

        help_text = (
            "memberNo 파일 형식\n"
            "- txt: 12345,23456 또는 줄바꿈 구분\n"
            "- csv: memberNo 컬럼 포함\n\n"
            "생성 파일\n"
            "- demand_merged_YYYYMM.csv : 월 청구 합계\n"
            "- product_demand_merged_YYYYMM.csv : 서비스별 상세 RAW\n"
            "- 각 배치별 원본 json/csv\n"
        )
        self.log.insert("end", help_text)
        self.log.configure(state="disabled")

    def logger(self, message: str):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    def pick_member_file(self):
        path = filedialog.askopenfilename(filetypes=[("CSV/TXT", "*.csv *.txt"), ("All files", "*.*")])
        if path:
            self.vars["member_file"].set(path)

    def pick_output_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.vars["output_dir"].set(path)

    def clear_inputs(self):
        for key in ["access_key", "secret_key", "month", "member_file"]:
            self.vars[key].set("")

    def validate(self):
        required = ["base_url", "access_key", "secret_key", "month", "member_file", "output_dir"]
        for key in required:
            if not self.vars[key].get().strip():
                raise ValueError(f"{key} 값을 입력하세요.")
        month = self.vars["month"].get().strip()
        if len(month) != 6 or not month.isdigit():
            raise ValueError("조회 월은 YYYYMM 형식으로 입력하세요. 예: 202602")
        if not Path(self.vars["member_file"].get().strip()).exists():
            raise ValueError("memberNo 파일을 찾을 수 없습니다.")
        int(self.vars["member_batch_size"].get().strip())
        int(self.vars["page_size"].get().strip())

    def start_job(self):
        try:
            self.validate()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        self.run_btn.configure(state="disabled")
        threading.Thread(target=self._run_job, daemon=True).start()

    def _run_job(self):
        try:
            out_dir = collect_monthly(
                base_url=self.vars["base_url"].get().strip(),
                access_key=self.vars["access_key"].get().strip(),
                secret_key=self.vars["secret_key"].get().strip(),
                month=self.vars["month"].get().strip(),
                member_file=self.vars["member_file"].get().strip(),
                output_dir=self.vars["output_dir"].get().strip(),
                member_batch_size=int(self.vars["member_batch_size"].get().strip()),
                page_size=int(self.vars["page_size"].get().strip()),
                logger=self.logger,
            )
            self.logger(f"완료: {out_dir}")
            messagebox.showinfo(APP_TITLE, f"완료되었습니다.\n저장 위치:\n{out_dir}")
        except Exception as e:
            self.logger(f"오류: {e}")
            messagebox.showerror(APP_TITLE, str(e))
        finally:
            self.run_btn.configure(state="normal")


if __name__ == "__main__":
    app = App()
    app.mainloop()
