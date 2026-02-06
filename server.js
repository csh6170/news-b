const express = require('express');
const {spawn} = require('child_process');
const path = require('path');
const app = express();

app.use(express.static('public'));
app.get('/favicon.ico', (req, res) => res.status(204).end());

// [수정 포인트] 로직은 동일하나, 파이썬의 변경된 출력 형식을 안정적으로 받도록 유지
app.get('/summary', (req, res) => {
    const targetUrl = req.query.url;

    if (!targetUrl) {
        return res.status(400).send("URL 파라미터가 없습니다.");
    }

    // [추가] 시작 시간 기록
    const startTime = Date.now();
    console.log(`\n[Server] 분석 요청 시작: ${targetUrl}`);
    console.log(`[Time Log] 시작 시간: ${new Date(startTime).toLocaleTimeString()}`);

    // 실시간 응답(Streaming)을 위한 헤더 설정
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.setHeader('Transfer-Encoding', 'chunked');

    const python = spawn('python', ['summarizer.py', targetUrl], {
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' }
    });

    // 1. 파이썬의 표준 출력(최종 요약본 등) 처리
    python.stdout.on('data', (data) => {
        const output = data.toString();
        process.stdout.write(output); // 서버 터미널 출력
        res.write(data); // 브라우저로 즉시 전송
    });
    
    // 2. 파이썬의 표준 에러(진행 로그, API 에러 등) 처리
    python.stderr.on('data', (data) => {
        const errorOutput = data.toString();
        process.stderr.write(`[Python Log] ${errorOutput}`); // 서버 터미널 출력
        
        // 브라우저 UI 업데이트를 위해 필요한 로그만 선별 전송 (중복 제거 및 에러 포함)
        if (errorOutput.includes('Error') || 
            errorOutput.includes('Fatal') || 
            errorOutput.includes('API Error') ||
            errorOutput.includes('Debug')){
            res.write(data); // 브라우저로 즉시 전송
        }
    });

    // 3. 프로세스 종료 처리
    python.on('close', (code) => {
        // [추가] 종료 시간 기록 및 소요 시간 계산
        const endTime = Date.now();
        const duration = (endTime - startTime) / 1000; // 초 단위 변환

        console.log(`\n[Server] Python 프로세스 종료 (Code: ${code})`);
        console.log(`[Time Log] 종료 시간: ${new Date(endTime).toLocaleTimeString()}`);
        console.log(`[Time Log] ⏱️ 총 소요 시간: ${duration.toFixed(2)}초`);
        
        if (code !== 0) {
            // 종료 코드가 0이 아닐 경우 에러 메시지 전송 후 종료
            res.write("\n[Error] 분석 중 오류가 발생했습니다.");
        }
        
        res.end(); // 브라우저와의 연결 종료 (스트림 완결)
    });
});

const server = app.listen(3000, () => console.log('Server started on http://localhost:3000'));
// AI 분석 시간을 고려하여 타임아웃을 5분으로 설정합니다.
server.timeout = 300000;