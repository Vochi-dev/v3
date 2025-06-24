from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import json
from pathlib import Path
import hashlib
import logging

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

class GenerateConfigRequest(BaseModel):
    enterprise_id: str

# --- Helper Functions ---
def get_node_by_id(nodes, node_id):
    """Находит узел в списке по его ID."""
    return next((node for node in nodes if node['id'] == node_id), None)

def get_target_node_id(edges, source_node_id, source_handle=None):
    """Находит ID целевого узла для данного исходного узла."""
    for edge in edges:
        if edge['source'] == source_node_id:
            if source_handle is None or edge.get('sourceHandle') == source_handle:
                return edge.get('target')
    return None
    
def get_all_target_node_ids(edges, source_node_id):
    """Находит все ID целевых узлов для данного исходного узла."""
    return [edge.get('target') for edge in edges if edge['source'] == source_node_id]


def generate_context_name(schema_id, node_id):
    """Генерирует уникальное имя контекста."""
    return hashlib.md5(f"{schema_id}-{node_id}".encode()).hexdigest()[:8]

def generate_department_context_name(enterprise_id, department_number):
     return hashlib.md5(f"dep-{enterprise_id}-{department_number}".encode()).hexdigest()[:8]

# --- Context Generators ---

def generate_pattern_check_context(schema_id, node, nodes, edges):
    """Генерирует диалплан для узла 'patternCheck'."""
    context_name = generate_context_name(schema_id, node['id'])
    # No "Entering..." NoOp, just like in the example
    lines = [
        f"[{context_name}]",
        f"exten => _X.,1,NoOp(To external from ${{CALLERID(num)}})",
        f"same => n,MixMonitor(${{UNIQUEID}}.wav)",
    ]
    
    target_node_id = get_target_node_id(edges, node['id'])
    if not target_node_id:
        return "" # Or handle error appropriately

    target_context_name = generate_context_name(schema_id, target_node_id)
    pattern = node.get('data', {}).get('patterns', [{}])[0].get('shablon', '')
    
    if pattern:
        lines.append(f'same => n,GotoIf($[{{REGEX("{pattern}" ${{EXTEN}})}}]?{target_context_name},${{EXTEN}},1)')
    # NO Hangup() at the end, as per the user's example
    return "\n".join(lines)


def generate_greeting_context(schema_id, node, nodes, edges):
    """Генерирует диалплан для узла 'greeting'."""
    context_name = generate_context_name(schema_id, node['id'])
    lines = [f"[{context_name}]", f"exten => _X.,1,NoOp"] # Minimalistic NoOp

    audio_file_data = node.get('data', {}).get('greetingFile', {})
    if audio_file_data and audio_file_data.get('name'):
         # Assuming path logic from before
        audio_path = f"music/{node.get('enterprise_id')}/start/{audio_file_data['name']}"
        lines.append(f"same => n,Playback({audio_path})")

    target_node_id = get_target_node_id(edges, node['id'])
    if target_node_id:
        target_context_name = generate_context_name(schema_id, target_node_id)
        lines.append(f"same => n,Goto({target_context_name},${{EXTEN}},1)")
    else:
        lines.append("same => n,Hangup()")
        
    return "\n".join(lines)

def generate_external_lines_context(schema_id, node, nodes, edges):
    """Генерирует диалплан для узла 'externalLines'."""
    context_name = generate_context_name(schema_id, node['id'])
    lines = [f"[{context_name}]", f"exten => _X.,1,NoOp"]

    external_lines = node.get('data', {}).get('external_lines', [])
    sorted_lines = sorted(external_lines, key=lambda x: x.get('priority', 99))

    for line in sorted_lines:
        line_id = line.get('line_id', '')
        if not line_id or '_' not in line_id: continue
        line_type, line_name = line_id.split('_', 1)
        # Using PJSIP for goip lines as it was correct
        if line_type == 'gsm':
            dial_string = f"PJSIP/goip_{line_name}/${{EXTEN}}"
            lines.append(f"same => n,Dial({dial_string},60)")
    
    lines.append("same => n,Hangup()")
    return "\n".join(lines)

def generate_department_context(enterprise_id, dept):
    dept_num = dept['department_number']
    members = dept['members']
    if not members:
        return ""
    
    context_name = generate_department_context_name(enterprise_id, dept_num)
    # Using SIP as per user example for internal calls
    dial_members = "&".join([f"SIP/{m}" for m in members])
    
    lines = [
        f"[{context_name}]",
        "exten => _X.,1,Noop",
        f"same => n,Macro(incall_dial,${{Trunk}},{dial_members})",
        f"same => n,Dial({dial_members},,tTkK)",
        "same => n,Hangup",
        "exten => h,1,NoOp(Call is end)",
        'exten => h,n,Set(AGISIGHUP="no")',
        "exten => h,n,StopMixMonitor()",
        "same => n,Macro(incall_end,${Trunk})"
    ]
    return "\n".join(lines)

# --- Словарь генераторов ---
NODE_GENERATORS = {
    'patternCheck': generate_pattern_check_context,
    'greeting': generate_greeting_context,
    'externalLines': generate_external_lines_context,
}

# --- Статические части конфига ---
def get_config_header(token):
    # "Symbol in symbol" from user's example
    return f"""[globals]
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
exten => _00X.,1,Goto(dialexecute,${{EXTEN:2}},1)"""

def get_config_footer():
    # "Symbol in symbol" from user's example
    return """[playbackivr]
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
#include extensions_custom.conf"""

@app.post("/generate_config")
async def generate_config(request: GenerateConfigRequest):
    enterprise_id = request.enterprise_id
    conn = None
    try:
        conn = await asyncpg.connect(DB_CONFIG)
        
        token = await conn.fetchval("SELECT name2 FROM enterprises WHERE number = $1", enterprise_id)
        if not token:
            raise HTTPException(status_code=404, detail=f"Enterprise '{enterprise_id}' not found")

        # Fetch data
        schema_records = await conn.fetch(
            "SELECT schema_id, schema_data FROM dial_schemas WHERE enterprise_id = $1 AND schema_name LIKE 'Исходящая%' ORDER BY schema_name", enterprise_id
        )
        department_records = await conn.fetch(
            """
            SELECT d.number AS department_number, array_agg(uip.phone_number) AS members
            FROM departments d
            JOIN department_members dm ON d.id = dm.department_id
            JOIN user_internal_phones uip ON dm.internal_phone_id = uip.id
            WHERE d.enterprise_number = $1 GROUP BY d.id
            """,
            enterprise_id,
        )

        # --- Generation ---
        
        all_managers_routing = {}
        for r in schema_records:
            schema = {"id": r['schema_id'], "data": json.loads(r['schema_data'])}
            start_node = get_node_by_id(schema['data']['nodes'], 'start-outgoing')
            if not start_node: continue
            
            managers = start_node.get('data', {}).get('phones', [])
            first_node_id = get_target_node_id(schema['data']['edges'], 'start-outgoing')
            if not managers or not first_node_id: continue
            
            target_context = generate_context_name(schema['id'], first_node_id)
            for phone in managers:
                all_managers_routing[phone] = target_context

        dialexecute_lines = [
            "[dialexecute]",
            "exten => _XXX,1,NoOp(Local call to ${EXTEN})",
            "same => n,MixMonitor(${UNIQUEID}.wav)",
            "same => n,NoOp(LOCALCALL=========================================================)",
            "same => n,Macro(localcall_start,${EXTEN})",
            "same => n,NoOp(LOCALCALL======================================================END)",
            "same => n,Dial(SIP/${EXTEN},,tTkK)",
            "exten => _XXXX.,1,NoOp(Call to ${EXTEN} from ${CHANNEL(name):4:3}) and ${CALLERID(num)})",
        ]
        
        sorted_managers = sorted(all_managers_routing.items(), key=lambda item: int(item[0]))

        for phone, context in sorted_managers:
            dialexecute_lines.append(f'same => n,GotoIf($["${{CHANNEL(name):4:3}}" = "{phone}"]?{context},${{EXTEN}},1)')
            dialexecute_lines.append(f'same => n,GotoIf($["${{CALLERID(num)}}" = "{phone}"]?{context},${{EXTEN}},1) ')

        # Department rules
        for dept in department_records:
            dept_num = dept['department_number']
            context_name = generate_department_context_name(enterprise_id, dept_num)
            dialexecute_lines.append(f"exten => {dept_num},1,Goto({context_name},${{EXTEN}},1)")

        dialexecute_lines.extend([
            "exten => _[+]X.,1,Goto(dialexecute,${EXTEN:1},1)",
            "exten => _00X.,1,Goto(dialexecute,${EXTEN:2},1)",
            "exten => h,1,NoOp(CALL=========================================================)",
            "same => n,Macro(localcall_end)",
            "same => n,NoOp(CALL======================================================END)",
        ])

        # --- First-level Child Contexts ---
        child_contexts = []
        
        # Department Contexts
        for dept in department_records:
            child_contexts.append(generate_department_context(enterprise_id, dept))

        # Outgoing Schema FIRST contexts
        for r in schema_records:
            schema_id, data = r['schema_id'], json.loads(r['schema_data'])
            nodes, edges = data['nodes'], data['edges']
            
            first_node_id = get_target_node_id(edges, 'start-outgoing')
            if not first_node_id: continue
            
            first_node = get_node_by_id(nodes, first_node_id)
            if not first_node: continue
            
            # Generate context ONLY for this first node (which should be 'patternCheck')
            node_type = first_node.get('type')
            if node_type == 'patternCheck':
                context_str = generate_pattern_check_context(schema_id, first_node, nodes, edges)
                if context_str: child_contexts.append(context_str)

        # --- Assembly ---
        config_parts = [
            get_config_header(token),
            "\n".join(dialexecute_lines),
        ]
        config_parts.extend(child_contexts) # Add ONLY the direct children
        config_parts.append(get_config_footer())

        final_config = "\n\n".join(filter(None, config_parts))

        config_dir = Path(f"music/{enterprise_id}")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "extensions.conf"
        with open(config_path, "w") as f:
            f.write(final_config)

        return {"message": "Config generated", "path": str(config_path), "config": final_config}

    except Exception as e:
        logging.error(f"Error generating config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn and not conn.is_closed():
            await conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006, log_config="log_config.json")