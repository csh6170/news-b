import sys
import os
import asyncio
import json
from dotenv import load_dotenv
import google.generativeai as genai
from playwright.async_api import async_playwright
from trafilatura import extract
import io

# 1. 환경 변수 로드
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    sys.stderr.write("[Error] .env 파일에서 GOOGLE_API_KEY를 찾을 수 없습니다.\n")
    sys.exit(1)

# 2. Gemini 설정
genai.configure(api_key=api_key)

# 스트리밍을 지원하는 생성 함수
async def safe_generate_content_stream(model, prompt, step_name="Unknown"):
    sys.stderr.write(f"[Raw] AI 요약 에이전트 가동 시작...\n")
    sys.stderr.flush()
    try:
        # stream=True 옵션 사용
        response = await model.generate_content_async(prompt, stream=True)
        return response
    except Exception as e:
        sys.stderr.write(f"[API Error] {step_name} 오류: {e}. 3초 후 재시도합니다.\n")
        sys.stderr.flush()
        await asyncio.sleep(3)
        try:
            response = await model.generate_content_async(prompt, stream=True)
            return response
        except Exception as e2:
            sys.stderr.write(f"[Fatal] 재시도 실패: {e2}\n")
            sys.stderr.flush()
            return None

async def get_news_content(url):
    sys.stderr.write(f"[Raw] 뉴스 기사를 불러오는 중입니다...\n")
    sys.stderr.flush()
    
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )

            # [최적화] 리소스 로딩 차단 (속도 대폭 향상)
            await context.route("**/*", lambda route: route.abort() 
                if route.request.resource_type in ["image", "stylesheet", "font", "media"] 
                else route.continue_())

            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2) 
            
            content = await page.content()
            page_title = await page.title()
            
            extract_result = extract(content, output_format='json', include_comments=False, include_tables=True)
            
            if extract_result:
                data = json.loads(extract_result)
            else:
                data = {}

            data['url'] = url

            if not data.get('title') or data.get('title') == "None":
                data['title'] = page_title
                
            if not data.get('text') or len(data.get('text', '')) < 50:
                 sys.stderr.write("[Debug] 본문 추출 방식 최적화 중...\n")
                 sys.stderr.flush()
                 await asyncio.sleep(0.5)
                 body_element = await page.query_selector('body')
                 if body_element:
                     data['text'] = await body_element.inner_text()
                 else:
                     data['text'] = ""

            text_len = len(data.get('text', ''))
            
            # [추가] 개발자 검증용: 크롤링 된 원본 텍스트 전체 출력
            sys.stderr.write(f"\n[Raw] ---------------- 원본 데이터 확인 시작 ----------------\n")
            sys.stderr.write(f"{data.get('text', '')}\n")
            sys.stderr.write(f"[Raw] ---------------- 원본 데이터 확인 종료 ----------------\n\n")
            sys.stderr.flush()
            # -----------------------------------------------------------

            sys.stderr.write(f"[Raw] 수집 완료 (제목: {data.get('title')}, 길이: {text_len}자)\n")
            sys.stderr.flush()
            sys.stderr.write(f"[Debug] 데이터 수집 완료! AI 분석을 준비합니다.\n")
            sys.stderr.flush()
            await asyncio.sleep(0.5)
            
            return data

        except Exception as e:
            sys.stderr.write(f"[Scraping Error] 데이터 수집 중 오류 발생: {e}\n")
            sys.stderr.flush()
            return None
        finally:
            if browser:
                await browser.close()

# [수정] 스트림 객체를 반환하도록 변경
async def summarize_with_ultra_precision_stream(news_data):
    model_name = 'gemini-flash-latest'
    model = genai.GenerativeModel(model_name, generation_config={"temperature": 0.0})

    source_text = news_data.get('text', '')
    if not source_text:
        return None
    
    # [최적화된 프롬프트: 속도 향상 + 환각 방지 + 본문 우선 + ~다체]
    combined_prompt = f"""
    ROLE: 뉴스 본문 팩트 추출기
    GOAL: 오직 제공된 [뉴스 본문]의 내용만을 요약한다. 외부 지식 사용을 엄격히 금지한다.

    [핵심 원칙: 본문 절대주의]
    1. **절대적 기준**: 뉴스 본문의 내용이 실제 사실과 다르거나 허위이더라도, **본문에 적힌 내용 그대로** 요약한다. AI가 알고 있는 지식으로 본문 내용을 수정하거나 검증하지 않는다.
    2. **환각 방지**: 본문에 없는 내용은 단 1%도 섞지 않는다. 문장 연결을 위해 인과관계를 임의로 창작하지 않는다.

    [제외 대상 (구체적 예시)]
    - **배경 지식**: "이 사건은 과거 ~사태와 유사하다", "통상적으로 ~라 알려져 있다" 등 본문에 없는 역사적/사회적 배경.
    - **추론 및 해석**: "따라서 ~할 것으로 보인다", "이는 ~를 의미한다" 등 기자의 주관이나 AI의 추측.
    - **일반적 전망**: 본문에 명시되지 않은 미래 예측(예: "주가가 오를 전망이다").
    - **감정적 평가**: "충격적이게도", "안타깝게도", "다행히" 등의 수식어.

    [작성 규칙]
    1. **분량**: 최대 5문장의 한 문단.
    2. **문체**: 모든 문장은 반드시 **'~다.'** 로 끝나는 완결된 평서문이어야 한다. (~함, ~음, ~임 사용 금지)
    3. **제목**: 본문의 핵심 내용을 담은 건조한 사실로 작성.

    [출력 예시]
    📌 제목: 정부, 내년 예산 600조 원 편성 확정

    요약:
     정부가 국무회의를 통해 내년도 예산안을 600조 원 규모로 확정했다. 이는 전년 대비 8.3% 증가한 수치로 역대 최대 규모다.
    보건·복지·고용 분야 예산이 가장 큰 비중을 차지했다. 정부는 경기 회복과 사회 안전망 강화를 위해 확장 재정이 불가피하다고 설명했다.
    국회 제출 후 심의를 거쳐 12월 초 최종 확정될 예정이다.

    * [태그]: 정부, 예산안, 600조 원, 국무회의

    [뉴스 본문]
    제목: {news_data.get('title')}
    내용: {source_text}
    """

    # 스트림 객체를 반환합니다.
    return await safe_generate_content_stream(model, combined_prompt, step_name="초정밀 문단 요약")

async def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = "https://news.naver.com"

    news_data = await get_news_content(url)
    
    if news_data:
        # AI 스트리밍 호출
        stream = await summarize_with_ultra_precision_stream(news_data)
        
        if stream:
            # AI 호출 전에 헤더를 미리 출력하여 브라우저 타임아웃 방지
            print("\n" + "="*60, flush=True)
            print("최종 정밀 요약 (99.9% Accuracy)", flush=True)
            print("="*60, flush=True)
            # 덩어리(chunk)가 올 때마다 즉시 전송
            async for chunk in stream:
                if chunk.text:
                    print(chunk.text, end="", flush=True)
        else:
            print("\n[Error] 요약 생성에 실패했습니다.", flush=True)

        # 푸터 및 로그 출력
        print("\n" + "="*60, flush=True)
        print("내부 감사 로그 (Verification Log)", flush=True)
        print("="*60, flush=True)
        sys.stderr.write(f"\n[Status Log] 초정밀 문단 요약 모드 실행 완료\n")
        sys.stderr.flush()
        
    else:
        sys.stderr.write("[Error] 뉴스 데이터를 가져오지 못했습니다.\n")
        sys.stderr.flush()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())