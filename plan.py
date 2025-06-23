from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import json
import os
from pathlib import Path

app = FastAPI()

# --- НАЧАЛО: Добавление CORS Middleware ---
origins = [
    "https://bot.vochi.by",
    "http://localhost",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- КОНЕЦ: Добавление CORS Middleware ---

DB_CONFIG = 'postgresql://postgres:r%2FYskqh%2FZbZuvjb2b3ahfg%3D%3D@127.0.0.1:5432/postgres'

class GenerationRequest(BaseModel):
    enterprise_id: str

@app.post("/generate_config")
async def generate_config(request: GenerationRequest):
    """
    Этот эндпоинт будет вызываться из сервиса dial
    для генерации конфигурационного файла Asterisk на основе схемы.
    """
    conn = None
    try:
        conn = await asyncpg.connect(DB_CONFIG)
        enterprise_id = request.enterprise_id
        
        # 1. Получаем токен (name2) из таблицы enterprises по полю number
        enterprise_record = await conn.fetchrow(
            "SELECT name2 FROM enterprises WHERE number = $1", enterprise_id
        )
        if not enterprise_record or not enterprise_record['name2']:
            raise HTTPException(status_code=404, detail=f"Enterprise or its token (name2) not found for number {enterprise_id}")
        
        token = enterprise_record['name2']

        # 2. Находим последнюю обновленную ИСХОДЯЩУЮ схему для данного предприятия
        schema_record = await conn.fetchrow(
            """
            SELECT schema_id, schema_name, schema_data FROM dial_schemas
            WHERE enterprise_id = $1 AND schema_name LIKE 'Исходящая%'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            enterprise_id
        )
        
        if not schema_record:
            # Если схем нет, ничего не делаем. Файл не создаем.
            return {"status": "ok", "message": "No outgoing schema found, nothing to generate."}

        schema_data = schema_record['schema_data']
        
        print("----- Получена схема из БД -----")
        print(json.dumps(schema_data, indent=2, ensure_ascii=False))
        print("--------------------------------")

        # 3. Формируем шапку диалплана
        # Двойные фигурные скобки {{...}} нужны, чтобы f-string не пытался их форматировать
        header = f"""[globals]
DIALOPTIONS = mtT
QUEUEOPTIONS= tTHh
RINGTIME = 30
OUTRINGTIME = 120
TRANSFER_CONTEXT=dialexecute
ID_TOKEN={token}

[default]
exten => s,1,NoOp(Qualify response)

[inoffice]
exten => _*5X,1,ParkedCall(default,${{EXTEN:2}})
exten => *65,1,Answer()
exten => *65,n,Playback(hello-world)
exten => *65,n,Playback(demo-congrats)
exten => *65,n,Echo()
exten => *65,n,Hangup()
exten => _X.,1,Set(AUDIOHOOK_INHERIT(MixMonitor)=yes)
exten => _X.,n,MixMonitor(${{UNIQUEID}}.wav)
exten => _X.,n,Wait(1)
exten => _X.,n,Goto(dialexecute,${{EXTEN}},1)
exten => _X.,n,System(/bin/echo '${{STRFTIME(${{EPOCH}},,%d-%m-%Y-%H_%M)}}--${{CALLERID(num)}}--${{EXTEN}}' >>/var/log/asterisk/service)
exten => _X.,n,Answer()
exten => _X.,n,Goto(waitredirect,${{EXTEN}},1)
exten => _00XXX,1,Confbridge(${{EXTEN:2}})
exten => _01XXX,1,Chanspy(SIP/${{EXTEN:2}},bqw)
exten => _02XXX,1,Chanspy(SIP/${{EXTEN:2}},Bbqw)
exten => _07XXX,1,AGI(perexvat.php,${{EXTEN:2}}:${{CHANNEL}}:1)
exten => _07XXX,2,Hangup()
exten => _08XXX,1,AGI(perexvat.php,${{EXTEN:2}}:${{CHANNEL}}:0)
exten => _09XXX,1,Chanspy(SIP/${{EXTEN:2}},bq)
exten => 750,1,Confbridge(750)
exten => 0,1,Confbridge(${{DIALEDPEERNUMBER}})
exten => 555,1,Answer()
exten => 555,2,Echo()
exten => _[+]X.,1,Goto(dialexecute,${{EXTEN:1}},1)
exten => _00X.,1,Goto(dialexecute,${{EXTEN:2}},1)
"""

        footer = """
[playbackivr]
exten => _X.,1,Noop(start playback ivr ${FILEPLAYBACK} ${WAITEXTEN})
exten => _X.,2,Background(custom/${FILEPLAYBACK})
exten => _X.,3,WaitExten(${WAITEXTEN})
exten => _X.,4,Goto(waitredirect,${EXTEN},1)

[playback]
exten => _X.,1,Noop(Start Playback ${FILEPLAYBACK})
exten => _X.,2,Answer()
exten => _X.,3,Playback(custom/${FILEPLAYBACK})
exten => _X.,4,Goto(waitredirect,${EXTEN},1)

[waitredirect]
exten => _X.,1,Noop(wait for redirect ${CHANNEL} - ${CALLERID(all)})
exten => _X.,2,Wait(10)
exten => _X.,3,Goto(apphangup,${EXTEN},1)

[apphangup]
exten => _X.,1,Hangup(17)

[appchanspy]
exten => _X.,1,Noop(start chanspy ${SPYSTRING})
exten => _X.,2,ChanSpy(${SPYSTRING},qv(-1))

[appchanspywhisp]
exten => _X.,1,Noop(start chanspywhisp ${SPYSTRING})
exten => _X.,2,ChanSpy(${SPYSTRING},wqv(-1))

[appconfbridge]
exten => _X.,1,Noop(Start confernce - ${CONFSTRING})
exten => _X.,2,ConfBridge(${CONFSTRING})

[sip-providers]
exten => _X.,1,UserEvent(PROVIDERS:${CALLERID(num)}:${EXTEN})
exten => _X.,2,Set(AUDIOHOOK_INHERIT(MixMonitor)=yes)
exten => _X.,3,Dial(SIP/180,,tTkK)
exten => s,1,UserEvent(PROVIDERS:)

[wapo]
exten => _9XX,1,Dial(Local/${EXTEN}@inoffice,,tTkK)
exten => _4XX,1,Dial(Local/${EXTEN}@inoffice,,tTkK)
exten => _XXX,1,Dial(SIP/${EXTEN},,tTkK)
exten => _XXXXXXXXXXX,1,NoOp(TRANK is: ${TRUNK})
same => n,Dial(SIP/0001302/2${EXTEN},,tTkK)
exten => 555,1,Answer
exten => 555,n,Echo()
exten => 0,1,NoOp(Conferenc)
same => n,DumpChan()
same => n,ConfBridge(${DIALEDPEERNUMBER})
same=>h,1,Wait(1)
exten => _08XXX,1,AGI(perexvat.php,${EXTEN:2}:${CHANNEL}:0)

[web-zapros]
exten => 1,1,Dial(${WHO},,tT)

;******************************Smart Redirection******************************************
;******************************Smart Redirection******************************************
#include extensions_custom.conf
"""

        config_content = header + footer
        
        # 4. Создаем директорию и сохраняем файл
        config_dir = Path(f"music/{enterprise_id}")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / 'extensions.conf'

        with open(config_path, 'w') as f:
            f.write(config_content)

        print(f"Конфигурационный файл для предприятия {enterprise_id} сохранен в {config_path}")
        
        return {"status": "ok", "message": f"Configuration for enterprise {enterprise_id} generated successfully in {config_path}"}

    except Exception as e:
        print(f"Ошибка при генерации конфигурации: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during config generation.")
    finally:
        if conn:
            await conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006, log_config="log_config.json") 