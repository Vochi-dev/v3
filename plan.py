from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import json
from pathlib import Path
import hashlib
import logging
import re

app = FastAPI()

# --- Logging Setup ---
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "plan.log", mode='w'),
        logging.StreamHandler()
    ],
    force=True
)

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

def find_first_meaningful_node(start_node_id, nodes, edges):
    """Находит ID первого 'значимого' узла, пропуская пустые транзитные узлы."""
    current_node_id = start_node_id
    visited = set()

    while current_node_id and current_node_id not in visited:
        visited.add(current_node_id)
        node = get_node_by_id(nodes, current_node_id)
        if not node:
            return None

        # Условие, при котором узел считается "пустым" и транзитным.
        # В данном случае - это узел "greeting" без указанного файла.
        is_passthrough = (
            node.get('type') == 'greeting' and
            not node.get('data', {}).get('greetingFile', {}).get('name')
        )

        if is_passthrough:
            # Узел пустой, ищем следующий.
            current_node_id = get_target_node_id(edges, current_node_id)
        else:
            # Найден значимый узел.
            return current_node_id
    
    return None # Обнаружен цикл или конец пути.

def generate_context_name(schema_id, node_id):
    """Генерирует уникальное имя контекста."""
    return hashlib.md5(f"{schema_id}-{node_id}".encode()).hexdigest()[:8]

def generate_department_context_name(enterprise_id, department_number):
     return hashlib.md5(f"dep-{enterprise_id}-{department_number}".encode()).hexdigest()[:8]

def generate_dial_in_context(schema_id, node, nodes, edges, music_files_info, dialexecute_contexts_map):
    """Генерирует диалплан для узла 'Звонок на список номеров'."""
    logging.info(f"Generating dial_in context for node {node['id']} in schema {schema_id}")
    context_name = generate_context_name(schema_id, node['id'])
    
    data = node.get('data', {})
    managers_flat = data.get('managers', [])
    logging.info(f"Node data: {data}")
    logging.info(f"Managers flat: {managers_flat}")
    wait_time = data.get('waitingRings', 3) * 5
    music_data = data.get('holdMusic', {'type': 'default'})
    music_option = music_data.get('type', 'default')

    # 1. Определяем опции музыки (пока заглушка, будет расширено)
    dial_options = "TtKk"
    if music_option == 'default':
        dial_options = "m" + dial_options
    elif music_option == 'custom':
        music_name = music_data.get('name')
        if music_name and music_name in music_files_info:
            internal_filename = music_files_info[music_name]['internal_filename'].replace('.wav', '')
            dial_options = f"m({internal_filename})" + dial_options

    # 2. Группируем номера (пока базовая логика)
    internal_numbers = [m['phone'] for m in managers_flat if m['phone'].isdigit() and len(m['phone']) <= 4]
    dial_command_string = "&".join([f"SIP/{num}" for num in internal_numbers])
    
    if not dial_command_string:
        logging.warning(f"No internal numbers found for node {node['id']}. Skipping context generation.")
        return "" # Некому звонить
    
    logging.info(f"Dial command string: {dial_command_string}")

    # 3. Собираем контекст
    lines = [
        f"[{context_name}]",
        "exten => _X.,1,Noop",
        f"same => n,Dial({dial_command_string},{wait_time},{dial_options})",
    ]

    # 4. Проверяем следующий узел
    target_node_id = get_target_node_id(edges, node['id'])
    if target_node_id:
        final_target_id = find_first_meaningful_node(target_node_id, nodes, edges)
        if final_target_id:
            target_context_name = generate_context_name(schema_id, final_target_id)
            lines.append(f"same => n,Goto({target_context_name},${{EXTEN}},1)")

    lines.append("same => n,Hangup")
    lines.extend([
        "exten => h,1,NoOp(Call is end)",
        'exten => h,n,Set(AGISIGHUP="no")',
        "exten => h,n,StopMixMonitor()",
        "same => n,Macro(incall_end,${Trunk})"
    ])
    
    return "\n".join(lines)

# --- Context Generators ---

def generate_pattern_check_context(schema_id, node, nodes, edges):
    """Генерирует диалплан для узла 'patternCheck'."""
    context_name = generate_context_name(schema_id, node['id'])
    lines = [
        f"[{context_name}]",
        f"exten => _X.,1,NoOp(To external from ${{CALLERID(num)}})",
        f"same => n,MixMonitor(${{UNIQUEID}}.wav)",
    ]

    child_edges = [edge for edge in edges if edge['source'] == node['id']]

    for edge in child_edges:
        target_id = edge.get('target')
        if not target_id: continue

        child_node = get_node_by_id(nodes, target_id)
        if not child_node: continue

        pattern_name = child_node.get('data', {}).get('label')
        
        pattern_data = next((p for p in node.get('data', {}).get('patterns', []) if p.get('name') == pattern_name), None)
        if not pattern_data or not pattern_data.get('shablon'):
            continue
            
        pattern_shablon = pattern_data['shablon']

        final_target_id = find_first_meaningful_node(target_id, nodes, edges)

        if final_target_id:
            target_context_name = generate_context_name(schema_id, final_target_id)
            lines.append(f'same => n,GotoIf($[{{REGEX("{pattern_shablon}" ${{EXTEN}})}}]?{target_context_name},${{EXTEN}},1)')
            
    return "\n".join(lines)


def generate_greeting_context(schema_id, node, nodes, edges):
    """Генерирует диалплан для узла 'greeting'."""
    # Не генерируем контекст для пустого узла.
    if not node.get('data', {}).get('greetingFile', {}).get('name'):
        return ""

    context_name = generate_context_name(schema_id, node['id'])
    lines = [f"[{context_name}]", f"exten => _X.,1,NoOp"] 

    audio_file_data = node.get('data', {}).get('greetingFile', {})
    audio_path = f"music/{node.get('enterprise_id')}/start/{audio_file_data['name']}"
    lines.append(f"same => n,Playback({audio_path})")

    target_node_id = get_target_node_id(edges, node['id'])
    if target_node_id:
        final_target_id = find_first_meaningful_node(target_node_id, nodes, edges)
        if final_target_id:
            target_context_name = generate_context_name(schema_id, final_target_id)
            lines.append(f"same => n,Goto({target_context_name},${{EXTEN}},1)")
        else:
            lines.append("same => n,Hangup()")
    else:
        lines.append("same => n,Hangup()")
        
    return "\n".join(lines)

def generate_external_lines_context(schema_id, node, nodes, edges, gsm_lines_info, sip_unit_info):
    """Генерирует диалплан для узла 'externalLines' с учетом типа линии (GSM/SIP)."""
    context_name = generate_context_name(schema_id, node['id'])
    lines = [f"[{context_name}]", "exten => _X.,1,NoOp"]

    external_lines = node.get('data', {}).get('external_lines', [])
    
    if not external_lines:
        lines.append("same => n,Hangup()")
    else:
        for line in sorted(external_lines, key=lambda x: x.get('priority', 99)):
            line_id_full = line.get('line_id', '')

            if line_id_full.startswith('gsm_'):
                line_id = line_id_full.split('_', 1)[1]
                gsm_info = gsm_lines_info.get(line_id)
                if not gsm_info or 'prefix' not in gsm_info:
                    continue
                prefix = gsm_info['prefix']
                lines.append(f"same => n,Macro(outcall_dial,{line_id},${{EXTEN}})")
                lines.append(f"same => n,Dial(SIP/{line_id}/{prefix}${{EXTEN}},,tTkK)")

            elif line_id_full.startswith('sip_'):
                line_name = line_id_full.split('_', 1)[1]
                sip_info = sip_unit_info.get(line_name)
                if not sip_info:
                    continue

                prefix_str = sip_info.get('prefix')
                
                # Сценарий А: префикс с фигурными скобками
                if prefix_str and '{' in prefix_str and '}' in prefix_str:
                    match = re.match(r'([^\{]+)\{(\d+)\}', prefix_str)
                    if match:
                        prefix_part = match.group(1)
                        offset_val = int(match.group(2))
                        offset = 12 - offset_val
                        
                        lines.append(f"same => n,Macro(outcall_dial,{line_name},${{EXTEN}})")
                        lines.append(f"same => n,Set(CALLERID(num)={line_name})")
                        lines.append(f"same => n,Dial(SIP/{line_name}/{prefix_part}${{EXTEN:{offset}}},,tTkK)")
                # Сценарий Б: префикс отсутствует или простой
                else:
                    lines.append(f"same => n,Macro(outcall_dial,{line_name},${{EXTEN}})")
                    lines.append(f"same => n,Dial(SIP/{line_name}/${{EXTEN}},,tTkK)")

        lines.append("same => n,Hangup")
        lines.extend([
            "exten => h,1,NoOp(Call is end)",
            'exten => h,n,Set(AGISIGHUP="no")',
            "exten => h,n,StopMixMonitor()",
            "same => n,Macro(outcall_end,${Trunk})"
        ])
        
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
    'externalLines': lambda schema_id, node, nodes, edges, gsm_lines_info, sip_unit_info: generate_external_lines_context(schema_id, node, nodes, edges, gsm_lines_info, sip_unit_info),
    'dialIn': generate_dial_in_context,
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
    logging.info(f"--- Starting config generation for enterprise: {enterprise_id} ---")
    conn = None
    try:
        conn = await asyncpg.connect(DB_CONFIG)
        
        token = await conn.fetchval("SELECT name2 FROM enterprises WHERE number = $1", enterprise_id)
        if not token:
            raise HTTPException(status_code=404, detail=f"Enterprise '{enterprise_id}' not found")

        # --- Предварительная загрузка данных ---
        schema_records = await conn.fetch("SELECT * FROM dial_schemas WHERE enterprise_id = $1", enterprise_id)
        logging.info(f"Found {len(schema_records)} schemas for enterprise {enterprise_id}")
        department_records = await conn.fetch("SELECT d.number AS department_number, array_agg(uip.phone_number) AS members FROM departments d JOIN department_members dm ON d.id = dm.department_id JOIN user_internal_phones uip ON dm.internal_phone_id = uip.id WHERE d.enterprise_number = $1 GROUP BY d.id", enterprise_id)
        gsm_lines_records = await conn.fetch("SELECT * FROM gsm_lines WHERE enterprise_number = $1", enterprise_id)
        gsm_lines_info = {rec['line_id']: {'prefix': rec['prefix']} for rec in gsm_lines_records}
        sip_unit_records = await conn.fetch("SELECT * FROM sip_unit WHERE enterprise_number = $1", enterprise_id)
        sip_unit_info = {rec['line_name']: {'prefix': rec['prefix']} for rec in sip_unit_records}
        music_files_records = await conn.fetch("SELECT * FROM music_files WHERE enterprise_number = $1", enterprise_id)
        music_files_info = {file['display_name']: file for file in music_files_records}

        # --- Этап 1: Генерация dialexecute и карты маршрутов для Local/
        dialexecute_contexts_map = {}
        for r in schema_records:
            if r.get('schema_type') != 'outgoing': continue
            schema = {"id": r['schema_id'], "data": json.loads(r['schema_data'])}
            start_node = get_node_by_id(schema['data']['nodes'], 'start-outgoing')
            if not start_node: continue
            managers = start_node.get('data', {}).get('phones', [])
            first_node_id = get_target_node_id(schema['data']['edges'], 'start-outgoing')
            if not managers or not first_node_id: continue
            target_context = generate_context_name(schema['id'], first_node_id)
            for phone in managers:
                dialexecute_contexts_map[phone] = target_context
        
        dialexecute_lines = [
            "[dialexecute]",
            "exten => _XXX,1,NoOp(Local call to ${EXTEN})",
            "same => n,Dial(SIP/${EXTEN},,tTkK)",
            "exten => _XXXX.,1,NoOp(Call to ${EXTEN} from ${CHANNEL(name):4:3}) and ${CALLERID(num)})",
        ]
        for phone, context in sorted(dialexecute_contexts_map.items(), key=lambda item: int(item[0])):
            dialexecute_lines.append(f'same => n,GotoIf($["${{CHANNEL(name):4:3}}" = "{phone}"]?{context},${{EXTEN}},1)')
            dialexecute_lines.append(f'same => n,GotoIf($["${{CALLERID(num)}}" = "{phone}"]?{context},${{EXTEN}},1) ')
        
        for dept in department_records:
            dept_num = dept['department_number']
            context_name = generate_department_context_name(enterprise_id, dept_num)
            dialexecute_lines.append(f"exten => {dept_num},1,Goto({context_name},${{EXTEN}},1)")

        dialexecute_lines.extend([
            "exten => _[+]X.,1,Goto(dialexecute,${{EXTEN:1}},1)",
            "exten => _00X.,1,Goto(dialexecute,${{EXTEN:2}},1)",
            "exten => h,1,NoOp(CALL=========================================================)",
            "same => n,Macro(localcall_end)",
            "same => n,NoOp(CALL======================================================END)",
        ])

        # --- Этап 2: Генерация всех контекстов с разделением ---
        pre_from_out_office_contexts = [generate_department_context(enterprise_id, dept) for dept in department_records]
        post_from_out_office_contexts = []

        # Обновленный NODE_GENERATORS
        NODE_GENERATORS_UPDATED = {
            'patternCheck': generate_pattern_check_context,
            'greeting': generate_greeting_context,
            'externalLines': generate_external_lines_context,
            'dial': generate_dial_in_context,
        }

        for r in schema_records:
            schema_id, data, schema_type = r['schema_id'], json.loads(r['schema_data']), r.get('schema_type', 'outgoing')
            logging.info(f"Processing schema_id: {schema_id}, schema_type: {schema_type}")
            nodes, edges = data['nodes'], data['edges']
            
            context_list = post_from_out_office_contexts if schema_type == 'incoming' else pre_from_out_office_contexts
            
            for node in nodes:
                node_type = node.get('type')
                node['enterprise_id'] = enterprise_id 
                
                if node_type == 'dial' and schema_type == 'incoming':
                     logging.info(f"Found 'dial' node ({node['id']}) in 'incoming' schema ({schema_id}). Calling generator.")

                if node_type in NODE_GENERATORS_UPDATED:
                    generator_func = NODE_GENERATORS_UPDATED[node_type]
                    context_str = None
                    if node_type == 'externalLines':
                        context_str = generator_func(schema_id, node, nodes, edges, gsm_lines_info, sip_unit_info)
                    elif node_type == 'dial':
                        if schema_type == 'incoming':
                            context_str = generator_func(schema_id, node, nodes, edges, music_files_info, dialexecute_contexts_map)
                    else: # Для остальных (greeting, patternCheck)
                        context_str = generator_func(schema_id, node, nodes, edges)
                    
                    if context_str:
                        context_list.append(context_str)

        logging.info(f"Generated {len(pre_from_out_office_contexts)} pre-contexts and {len(post_from_out_office_contexts)} post-contexts.")

        # --- Этап 3: Генерация from-out-office ---
        from_out_office_lines = [
            "[from-out-office]",
            "exten => _X.,1,Set(AUDIOHOOK_INHERIT(MixMonitor)=yes)",
            "same => n,Set(Trunk=${EXTEN})",
            "same => n,Answer",
            "same => n,Macro(incall_start,${Trunk})",
            "same => n,Set(CALLERID(num)=${CALLERID(name)})",
            "same => n,Set(CALLERID(name)=${NEWNAME}-${CALLERID(name)})",
            "same => n,Set(CDR(userfield)=${NEWNAME}-${CALLERID(name)})",
            "same => n,Answer",
            "exten => _X.,n,MixMonitor(${UNIQUEID}.wav)",
            "exten => _X.,n,NoOp(NOW is ${CALLERID(num)})"
        ]
        lines_with_context = []
        
        # GSM Lines
        incoming_gsm_lines = [line for line in gsm_lines_records if line['in_schema'] is not None]
        for line in sorted(incoming_gsm_lines, key=lambda x: int(x['line_id'])):
            schema = next((s for s in schema_records if s['schema_name'] == line['in_schema'] and s.get('schema_type') == 'incoming'), None)
            if schema:
                nodes, edges = json.loads(schema['schema_data'])['nodes'], json.loads(schema['schema_data'])['edges']
                start_node = get_node_by_id(nodes, '1')
                if start_node:
                    target_node_id = find_first_meaningful_node(start_node['id'], nodes, edges)
                    if target_node_id:
                        context_name = generate_context_name(schema['schema_id'], target_node_id)
                        lines_with_context.append({'line_id': line['line_id'], 'context': context_name})
        
        # SIP Lines
        incoming_sip_lines = [line for line in sip_unit_records if line['in_schema'] is not None]
        for line in sorted(incoming_sip_lines, key=lambda x: x['id']):
            schema = next((s for s in schema_records if s['schema_name'] == line['in_schema'] and s.get('schema_type') == 'incoming'), None)
            if schema:
                nodes, edges = json.loads(schema['schema_data'])['nodes'], json.loads(schema['schema_data'])['edges']
                start_node = get_node_by_id(nodes, '1')
                if start_node:
                    target_node_id = find_first_meaningful_node(start_node['id'], nodes, edges)
                    if target_node_id:
                        context_name = generate_context_name(schema['schema_id'], target_node_id)
                        lines_with_context.append({'line_id': line['line_name'], 'context': context_name})

        for item in lines_with_context:
            from_out_office_lines.append(f'exten => _X.,n,GotoIf($["${{EXTEN}}" = "{item["line_id"]}"]?{item["context"]},${{EXTEN}},1)')
        
        from_out_office_lines.extend([
            "exten => _X.,n,Hangup",
            "exten => h,1,NoOp(Call is end)",
            "exten => h,n,Set(AGISIGHUP=\"no\")",
            "exten => h,n,StopMixMonitor()",
            "same => n,Macro(incall_end,${Trunk})"
        ])

        # --- Этап 4: Финальная сборка ---
        config_parts = [
            get_config_header(token),
            "\n".join(dialexecute_lines),
        ]
        config_parts.extend(filter(None, pre_from_out_office_contexts))
        config_parts.append("\n".join(from_out_office_lines))
        config_parts.extend(filter(None, post_from_out_office_contexts))
        config_parts.append(get_config_footer())

        final_config = "\n\n".join(filter(None, config_parts))
        logging.info(f"Final config generated. Length: {len(final_config)}. Writing to file.")

        config_dir = Path(f"music/{enterprise_id}")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "extensions.conf"
        with open(config_path, "w") as f:
            f.write(final_config)

        logging.info(f"--- Finished config generation for enterprise: {enterprise_id} ---")
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