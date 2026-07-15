import requests
from bs4 import BeautifulSoup
import re
import json
import time
from datetime import datetime
from supabase import create_client, Client

# =========================================================================
# [설정 정보 입력]
# 1. 네이버 카페 정보 (다중 출석체크 메뉴 지원을 위해 리스트 형식으로 입력)
NAVER_CAFE_ID = "27959802" 
BOARD_MENU_IDS = ["28", "29", "30"]  # 예: ["28", "29", "30"] 형태로 여러 개 등록 가능

import os
# 코드 내에 직접 키를 적지 않고, GitHub Actions에 등록한 값을 불러옵니다.
NAVER_COOKIE = os.getenv("NAVER_COOKIE")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 외부 서비스 클라이언트 초기화
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def ask_gemini_batch_parse(items_to_parse):
    """
    구글 공식 REST API v1beta 게이트웨이를 사용하여 
    responseMimeType(JSON 출력 제어)을 완벽하게 호환 작동시킵니다.
    """
    if not GEMINI_API_KEY.strip() or not items_to_parse:
        return []
        
    today_str = datetime.today().strftime('%Y-%m-%d')
    
    prompt = f"""
    너는 네이버 카페 출석체크 한줄평 내용을 정밀 분석하여 헬스장/스튜디오 수업 예약 데이터를 가공하는 비서 엔진이야.
    오늘의 기준 날짜는 {today_str}이야.
    
    아래의 출석체크 JSON 목록을 엄격히 분석해서 정형화된 JSON 배열(List) 형태로만 답변해줘. 마크다운이나 다른 설명글은 일절 금지해.
    
    [출석체크 목록]:
    {json.dumps(items_to_parse, ensure_ascii=False, indent=2)}
    
    [★ StudentName 추출 핵심 규칙 - 절대 엄수]:
    1. unique_key: 입력 목록에 매핑된 unique_key 값을 그대로 토큰 유실 없이 반환해줘.
    2. StudentName: 글을 작성한 ID/닉네임(writer)인 "곰pd" 같은 닉네임 값은 '절대' 회원명으로 삼으면 안 돼. 
       - 본문 내용(text) 속에서 작성자 닉네임을 제외하고, 사용자가 직접 타이핑한 순수 예약 본문 중간에 위치한 '실제 사람 이름(한글 2~4글자)'을 추출해줘.
    3. Date: 오늘 날짜인 '{today_str}' 형식 고정 매핑.
    4. Time: HH:MM 형식의 24시간제 최종 시간으로 가공해서 반환해. 
       - "630" -> "06:30", "1000" -> "10:00" 처럼 3~4자리 숫자는 기존대로 파싱해.
       - 중요: 만약 예약 글 이름 앞에 "10", "18", "19", "20" 처럼 분(00)이 생략된 1자리 혹은 2자리 정수 형태로만 시간이 적혀있다면 정밀하게 뒤에 '00'분을 채워서 "10:00", "18:00", "19:00", "20:00" 형태로 변환해야 해. 절대로 "00:00"으로 누락하여 처리하면 안 돼.
       - 또한 "18:00->20:00 변경"과 같은 문맥 시 최종 타깃인 "20:00"을 확정해.
    5. PhoneTail: 본문 텍스트(text)를 최우선으로 분석해. 
       - 특히 닉네임이 숫자(예: "9792")인 경우, 닉네임 값을 휴대폰 번호로 착각하지 말고 
         본문 문자열 속에 포함된 4자리 숫자를 정확히 찾아내.
    6. Status: 
       - 본문 text 내부 혹은 유저 입력 필드에 시간 표현이 3개 이상 과도하게 나열되어 있으면 무조건 "제외대상"으로 분류.
       - 취소, x, cancel 등의 취소 의사가 보이면 -> "취소"
       - 변경, 이동 등의 문맥이 확인되면 -> "변경"
       - 정상적인 예약 글인 경우 -> "정상 예약"
    7. Note: 시간 변경 건일 때만 "이전시간 -> 변경시간" 형태로 기록하고, 나머지는 "" 빈 문자열 처리.
    """
    
    base_endpoint = "https://generativelanguage.googleapis.com"
    url = f"{base_endpoint}/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    
    for attempt in range(3):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=45)
            
            if response.status_code == 200:
                res_json = response.json()
                ai_response_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                
                triple_backtick = "```"
                if ai_response_text.startswith(triple_backtick):
                    pattern_start = r"^" + triple_backtick + r"(?:json)?\s+"
                    pattern_end = r"\s+" + triple_backtick + r"$"
                    ai_response_text = re.sub(pattern_start, "", ai_response_text, flags=re.MULTILINE)
                    ai_response_text = re.sub(pattern_end, "", ai_response_text, flags=re.MULTILINE)
                
                return json.loads(ai_response_text.strip())
            else:
                print(f"⚠️ [시도 {attempt+1}/3] API 서버 에러 (HTTP {response.status_code}): {response.text}")
                
        except Exception as e:
            print(f"⚠️ [시도 {attempt+1}/3] 세션 지연 또는 타임아웃 발생 (3초 후 재시도): {e}")
            
        time.sleep(3.0)
            
    print("❌ [최종 장애] Gemini API 호출 불능 상태입니다. 네트워크나 API 키 상태를 확인하세요.")
    return None

def update_daily_stats_summary(today_str):
    """
    예약 기록을 집계하여 일자별 통계 테이블을 업데이트합니다.
    """
    try:
        db_records = supabase.table("reservations").select("res_time, status").eq("res_date", today_str).execute()
        morning_cnt = 0
        afternoon_cnt = 0
        total_cnt = 0
        
        for record in db_records.data:
            status = record.get("status")
            res_time = str(record.get("res_time", "00:00"))
            
            if status in ["정상 예약", "변경"]:
                total_cnt += 1
                try:
                    time_digits = re.findall(r'\d+', res_time)
                    if len(time_digits) >= 2:
                        hour = int(time_digits[0])
                        min = int(time_digits[1])
                    elif len(time_digits) == 1:
                        hour = int(time_digits[0])
                        min = 0
                    else:
                        hour, min = 0, 0
                        
                    if (hour * 60) + min <= 900:  # 15:00 기준 분할 (900분)
                        morning_cnt += 1
                    else:
                        afternoon_cnt += 1
                except:
                    afternoon_cnt += 1
                    
        stats_data = {
            "stat_date": today_str,
            "total_count": total_cnt,
            "morning_count": morning_cnt,
            "afternoon_count": afternoon_cnt
        }
        supabase.table("daily_stats").upsert(stats_data, on_conflict="stat_date").execute()
        now_time = datetime.now().strftime('%H:%M:%S') # 현재 시간 추출
        msg = f"[통계 테이블 자동 적재완료] {today_str} {now_time} -> 유효 총원: {total_cnt}명 (오전: {morning_cnt} / 오후: {afternoon_cnt})"
        print(f"📊 {msg}")
    except Exception as e:
        print(f"❌ daily_stats 통계 연산 에러: {e}")

def parse_time_to_minutes(time_str):
    try:
        matches = re.findall(r'(\d{1,2}):(\d{2})', time_str)
        if matches:
            h, m = map(int, matches[0])
            return h * 60 + m
    except:
        pass
    return 9999

def crawl_once():
    today_str = datetime.today().strftime('%Y-%m-%d')
    active_fetched_keys = set()
    raw_unordered_list = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Cookie": NAVER_COOKIE
    }

    for menu_id in BOARD_MENU_IDS:
        part_protocol = "https://"
        part_host = "m.cafe.naver.com"
        part_path = "/AttendanceView.nhn"
        url = f"{part_protocol}{part_host}{part_path}?search.clubid={NAVER_CAFE_ID}&search.menuid={menu_id}"
        
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code != 200:
                print(f"⚠️ [{menu_id}] 카페 페이지 접속 불가 (HTTP {response.status_code})")
                continue
                
            soup = BeautifulSoup(response.text, 'html.parser')
            attendance_clones = soup.select('.item_attendance, .list_attendance li, ul.ct_list > li, .cmt_body, .comment_item')
            if not attendance_clones:
                attendance_clones = soup.find_all('li', class_=re.compile(r'attendance|comment|cmt|list_item'))
            
            for item in attendance_clones:
                writer_elem = item.select_one('.nick, .name, .user, .writer, .name_area, .nick_area')
                text_elem = item.select_one('.txt, .comment_content, p, .text, .content, .text_area')
                time_elem = item.select_one('.time, .date, .date_area')
                
                if not writer_elem or not text_elem: continue
                
                writer = writer_elem.text.strip()
                attendance_text = text_elem.text.strip()
                
                if "출석하기" in attendance_text or "글자 내로 입력" in attendance_text or "대리예약 불가" in attendance_text: continue
                if "---" in attendance_text or "___" in attendance_text: continue
                    
                time_expressions = re.findall(r'(\d{1,2}시\s*\d{1,2}분|\d{1,2}:\d{2})', attendance_text)
                if len(time_expressions) >= 3: continue
                    
                reg_time = time_elem.text.strip() if time_elem else "00:00"
                unique_key = f"{writer}_{reg_time}"
                
                if unique_key not in active_fetched_keys:
                    active_fetched_keys.add(unique_key)
                    raw_unordered_list.append({
                        "unique_key": unique_key,
                        "writer": writer,
                        "text": attendance_text,
                        "reg_time_str": reg_time,
                        "hint": "닉네임이 번호와 동일할 수 있으니 본문 텍스트를 우선순위로 확인해"
                    })
        except Exception as e:
            print(f"❌ [{menu_id}] 크롤러 데이터 요청/파싱 장애: {e}")
            continue

    if not raw_unordered_list:
        update_daily_stats_summary(today_str)
        return 0, 0, 0

    # 시간순 정렬
    raw_unordered_list.sort(key=lambda x: parse_time_to_minutes(x["reg_time_str"]))

    try:
        existing_records = supabase.table("reservations").select("attendance_key").eq("res_date", today_str).execute()
        existing_keys = {r['attendance_key'] for r in existing_records.data}
    except Exception as e:
        existing_keys = set()

    new_items_to_parse = []
    for idx, item in enumerate(raw_unordered_list):
        generated_prefix = f"{today_str}_{idx:04d}"
        is_already_saved = any(k.startswith(generated_prefix) for k in existing_keys)
        
        if not is_already_saved:
            new_items_to_parse.append({
                "unique_key": f"{generated_prefix}_{item['unique_key']}",
                "writer": item["writer"],
                "text": item["text"],
                # 💡 [조치 완료] 네이버 등록 당시의 원본 시각(HH:MM)을 신규 큐 배열에 영구 보존 상속합니다.
                "reg_time_str": item["reg_time_str"]
            })

    print(f"🔍 실시간 감지: {len(raw_unordered_list)}건 / 신규 분석 대상: {len(new_items_to_parse)}건")

    success_count = 0
    fail_count = 0

    if new_items_to_parse:
        chunk_size = 5
        for i in range(0, len(new_items_to_parse), chunk_size):
            chunk = new_items_to_parse[i:i+chunk_size]
            parsed_results = ask_gemini_batch_parse(chunk)
            
            if parsed_results:
                for row in parsed_results:
                    if row.get("Status") == "제외대상": continue
                    
                    # 💡 [조치 완료] 배치 덩어리(chunk) 중에서 매핑되는 예약글을 역추적하여 원본 등록 시간을 구합니다.
                    matched_reg_time = "00:00"
                    for orig in chunk:
                        if orig["unique_key"] == row.get("unique_key"):
                            raw_time = orig.get("reg_time_str", "00:00")
                            # "어제 15:24" 등 난잡한 포맷에서 순수한 시:분(HH:MM)만 정규식으로 걸러냅니다.
                            time_match = re.search(r'(\d{1,2}):(\d{2})', raw_time)
                            if time_match:
                                matched_reg_time = f"{time_match.group(1).zfill(2)}:{time_match.group(2)}"
                            else:
                                matched_reg_time = raw_time
                            break
                    
                    # 💡 [조치 완료] 비고(note) 앞단에 [작성: HH:MM] 프리픽스를 붙여 저장합니다.
                    final_note = row.get("Note", "")
                    prefix_stamp = f"[작성: {matched_reg_time}]"
                    if final_note:
                        final_note = f"{prefix_stamp} {final_note}"
                    else:
                        final_note = prefix_stamp
                    
                    row_data = {
                        "attendance_key": row.get("unique_key"),
                        "student_name": row.get("StudentName", "미정"),
                        "res_date": row.get("Date", today_str),
                        "res_time": row.get("Time", "00:00"),
                        "phone_tail": str(row.get("PhoneTail", "미기입")),
                        "status": row.get("Status", "정상 예약"),
                        "note": final_note
                    }
                    try:
                        supabase.table("reservations").upsert(row_data, on_conflict="attendance_key").execute()
                        success_count += 1
                    except Exception as e:
                        fail_count += 1
            else:
                fail_count += len(chunk)
            
            time.sleep(3.5)

    # 카페에서 삭제된 글 추적 동기화
    try:
        db_records = supabase.table("reservations").select("attendance_key, status").eq("res_date", today_str).execute()
        for record in db_records.data:
            db_key = record.get("attendance_key")
            current_status = record.get("status")
            
            matched = any(db_key.endswith(k) for k in active_fetched_keys)
            if not matched and current_status not in ["취소", "취소(삭제함)"]:
                supabase.table("reservations").update({"status": "취소(삭제함)"}).eq("attendance_key", db_key).execute()
    except Exception as e:
        pass
        
    update_daily_stats_summary(today_str)
    return success_count, fail_count, len(raw_unordered_list)

if __name__ == "__main__":
    crawl_once()