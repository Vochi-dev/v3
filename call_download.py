#!/usr/bin/env python3
"""
Сервис для скачивания записей разговоров (call_download.py)
Порт: 8012

Функциональность:
- Централизованное управление записями разговоров
- Интеграция с Hetzner Object Storage (S3)
- API для получения списка записей
- Генерация временных ссылок для скачивания
- Автоматическая выгрузка записей из локальных хранилищ
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import logging
import os
import asyncio
import psycopg2

# Импорт нашего S3 клиента
try:
    from hetzner_s3_integration import HetznerS3Client
    from s3_config import S3_CONFIG, validate_s3_config
    S3_AVAILABLE = True
except ImportError as e:
    logging.warning(f"S3 интеграция недоступна: {e}")
    S3_AVAILABLE = False

# Импорт функций для работы с БД
try:
    import sys
    sys.path.append('app')
    from app.services.postgres import (
        update_call_recording_info, 
        get_call_recording_info,
        get_call_recording_by_token, 
        search_calls_with_recordings,
        init_pool
    )
    DB_AVAILABLE = True
except ImportError as e:
    logging.warning(f"PostgreSQL интеграция недоступна: {e}")
    DB_AVAILABLE = False

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/call_service.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Call Download Service",
    description="Сервис для управления записями телефонных разговоров",
    version="1.0.0"
)

@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске сервиса"""
    if DB_AVAILABLE:
        try:
            await init_pool()
            logger.info("PostgreSQL пул соединений инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}")
    else:
        logger.warning("PostgreSQL недоступен, некоторые функции будут ограничены")

# Модели данных
class RecordingSearchRequest(BaseModel):
    enterprise_number: str
    date_from: datetime
    date_to: datetime

class RecordingInfo(BaseModel):
    key: str
    enterprise_number: str
    call_unique_id: str
    size: int
    last_modified: datetime
    download_url: Optional[str] = None

class UploadRequest(BaseModel):
    enterprise_number: str
    call_unique_id: str
    local_file_path: str
    call_date: Optional[datetime] = None

# Глобальный S3 клиент
s3_client = None

def get_enterprise_name2(enterprise_number: str) -> str:
    """
    Получает name2 предприятия из базы данных
    
    Args:
        enterprise_number: Номер предприятия
        
    Returns:
        str: name2 предприятия или enterprise_number если не найден
    """
    try:
        conn = psycopg2.connect(
            host="localhost",
            database="postgres",
            user="postgres",
            password="r/Yskqh/ZbZuvjb2b3ahfg=="
        )
        
        with conn.cursor() as cursor:
            cursor.execute("SELECT name2 FROM enterprises WHERE number = %s", (enterprise_number,))
            result = cursor.fetchone()
            
            if result and result[0]:
                return result[0]
            else:
                logger.warning(f"Предприятие {enterprise_number} не найдено в БД или name2 пустой")
                return enterprise_number
                
    except Exception as e:
        logger.error(f"Ошибка получения name2 для предприятия {enterprise_number}: {e}")
        return enterprise_number
    finally:
        if 'conn' in locals():
            conn.close()

@app.on_event("startup")
async def startup_event():
    """Инициализация сервиса при запуске"""
    global s3_client
    
    logger.info("🚀 Запуск сервиса скачивания записей разговоров")
    logger.info("🔧 Проверка конфигурации S3...")
    
    if not S3_AVAILABLE:
        logger.warning("⚠️  S3 интеграция недоступна - работаем в режиме заглушки")
        return
    
    # Проверка конфигурации S3
    config_check = validate_s3_config()
    if not config_check['valid']:
        logger.error("❌ Ошибки конфигурации S3:")
        for issue in config_check['issues']:
            logger.error(f"   - {issue}")
        logger.warning("⚠️  Работаем без S3 интеграции")
        return
    
    # Создание S3 клиента
    try:
        s3_client = HetznerS3Client(
            access_key=S3_CONFIG['ACCESS_KEY'],
            secret_key=S3_CONFIG['SECRET_KEY'],
            region=S3_CONFIG['REGION']
        )
        
        # Проверка подключения
        usage = s3_client.get_storage_usage()
        logger.info(f"✅ S3 подключение установлено")
        logger.info(f"📊 Статистика хранилища: {usage['total_files']} файлов, {usage['total_size_mb']} MB")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации S3 клиента: {e}")
        s3_client = None

@app.get("/")
async def root():
    """Главная страница сервиса"""
    return {
        "service": "Call Download Service",
        "version": "1.0.0",
        "status": "running",
        "port": 8012,
        "s3_available": s3_client is not None,
        "endpoints": [
            "/recordings/search",
            "/recordings/upload", 
            "/recordings/download/{enterprise_number}/{call_id}",
            "/recordings/stats",
            "/health"
        ]
    }

@app.get("/health")
async def health_check():
    """Проверка состояния сервиса"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "s3_connection": False,
        "disk_space": None
    }
    
    # Проверка S3 подключения
    if s3_client:
        try:
            usage = s3_client.get_storage_usage()
            health_status["s3_connection"] = True
            health_status["s3_stats"] = usage
        except Exception as e:
            health_status["s3_connection"] = False
            health_status["s3_error"] = str(e)
    
    # Проверка дискового пространства
    try:
        import shutil
        disk_usage = shutil.disk_usage("/")
        health_status["disk_space"] = {
            "total_gb": round(disk_usage.total / (1024**3), 2),
            "used_gb": round((disk_usage.total - disk_usage.free) / (1024**3), 2),
            "free_gb": round(disk_usage.free / (1024**3), 2),
            "usage_percent": round((disk_usage.total - disk_usage.free) / disk_usage.total * 100, 2)
        }
    except Exception as e:
        health_status["disk_space_error"] = str(e)
    
    return health_status

@app.get("/recordings/stats")
async def get_storage_stats():
    """Получение статистики хранилища"""
    if not s3_client:
        raise HTTPException(status_code=503, detail="S3 интеграция недоступна")
    
    try:
        usage = s3_client.get_storage_usage()
        
        # Расчет стоимости
        base_cost = 4.99  # EUR/месяц базовая плата
        additional_cost = max(0, usage['total_size_gb'] - 1) * 4.99  # Дополнительная плата за TB
        total_cost = base_cost + additional_cost
        
        return {
            "storage_usage": usage,
            "cost_estimate": {
                "base_cost_eur": base_cost,
                "additional_cost_eur": round(additional_cost, 2),
                "total_monthly_cost_eur": round(total_cost, 2),
                "included_storage_gb": 1024,
                "included_traffic_gb": 1024
            },
            "bucket_info": {
                "name": S3_CONFIG['BUCKET_NAME'],
                "region": S3_CONFIG['REGION'],
                "endpoint": S3_CONFIG['ENDPOINT_URL']
            }
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка получения статистики: {str(e)}")

@app.post("/recordings/search")
async def search_recordings(request: RecordingSearchRequest):
    """Поиск записей разговоров по критериям"""
    if not s3_client:
        # Заглушка для работы без S3
        return {
            "recordings": [],
            "total_count": 0,
            "message": "S3 интеграция недоступна - возвращаем пустой результат"
        }
    
    try:
        recordings = s3_client.find_recordings(
            enterprise_number=request.enterprise_number,
            date_from=request.date_from,
            date_to=request.date_to
        )
        
        # Преобразуем в удобный формат
        result_recordings = []
        for recording in recordings:
            result_recordings.append({
                "key": recording['key'],
                "enterprise_number": request.enterprise_number,
                "call_unique_id": recording['key'].split('/')[-1].replace('.wav', ''),
                "size": recording['size'],
                "last_modified": recording['last_modified'],
                "download_url": recording['download_url']
            })
        
        return {
            "recordings": result_recordings,
            "total_count": len(result_recordings),
            "search_criteria": request.dict(),
            "message": f"Найдено {len(result_recordings)} записей"
        }
        
    except Exception as e:
        logger.error(f"Ошибка поиска записей: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка поиска записей: {str(e)}")

@app.get("/recordings/download/{enterprise_number}/{call_id}")
async def get_download_link(
    enterprise_number: str, 
    call_id: str,
    expires_in: int = Query(3600, description="Время жизни ссылки в секундах")
):
    """Генерация временной ссылки для скачивания записи"""
    if not s3_client:
        raise HTTPException(status_code=503, detail="S3 интеграция недоступна")
    
    try:
        # Получаем информацию о записи из БД (быстрый прямой доступ)
        if DB_AVAILABLE:
            call_info = await get_call_recording_info(call_id)
            
            if not call_info or not call_info.get('s3_object_key'):
                raise HTTPException(status_code=404, detail="Запись не найдена в БД")
            
            object_key = call_info['s3_object_key']
            
            # Проверяем что предприятие совпадает для безопасности
            if call_info['enterprise_id'] != enterprise_number:
                raise HTTPException(status_code=403, detail="Доступ к записи запрещен")
                
        else:
            # Fallback: старая логика поиска в S3 (если БД недоступна)
            logger.warning("БД недоступна, используем поиск в S3")
            enterprise_name2 = get_enterprise_name2(enterprise_number)
            prefix = f"CallRecords/{enterprise_name2}/"
            
            response = s3_client.s3_client.list_objects_v2(
                Bucket=s3_client.bucket_name,
                Prefix=prefix
            )
            
            object_key = None
            if 'Contents' in response:
                for obj in response['Contents']:
                    if call_id in obj['Key'] and obj['Key'].endswith('.mp3'):
                        object_key = obj['Key']
                        break
                    elif call_id in obj['Key'] and obj['Key'].endswith('.wav'):
                        object_key = obj['Key']
            
            if not object_key:
                raise HTTPException(status_code=404, detail="Запись не найдена в S3")
        
        # Генерируем временную ссылку
        download_link = s3_client.generate_download_link(object_key, expires_in)
        
        if download_link:
            result = {
                "download_url": download_link,
                "expires_in_seconds": expires_in,
                "expires_at": (datetime.now() + timedelta(seconds=expires_in)).isoformat(),
                "enterprise_number": enterprise_number,
                "call_id": call_id,
                "object_key": object_key
            }
            
            # Добавляем дополнительную информацию если есть данные из БД
            if DB_AVAILABLE and 'call_info' in locals():
                result.update({
                    "recording_duration": call_info.get('recording_duration'),
                    "call_duration": call_info.get('duration'),
                    "call_start_time": call_info.get('start_time').isoformat() if call_info.get('start_time') else None
                })
            
            return result
        else:
            raise HTTPException(status_code=404, detail="Не удалось сгенерировать ссылку")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка генерации ссылки: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации ссылки: {str(e)}")

@app.get("/recordings/file/{uuid_token}")
async def get_recording_file(uuid_token: str):
    """Прямой доступ к файлу записи по UUID токену (с ленивой загрузкой)"""
    if not s3_client:
        raise HTTPException(status_code=503, detail="S3 интеграция недоступна")
    
    if not DB_AVAILABLE:
        raise HTTPException(status_code=503, detail="База данных недоступна")
    
    try:
        # Получаем информацию о записи по UUID токену
        call_info = await get_call_recording_by_token(uuid_token)
        
        if not call_info:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        
        # Проверяем есть ли файл на S3
        if call_info.get('s3_object_key'):
            # ✅ Файл уже на S3 - отдаем его
            object_key = call_info['s3_object_key']
            download_link = s3_client.generate_download_link(object_key, 3600)
            
            if download_link:
                logger.info(f"🎯 Возвращаем готовый файл: {uuid_token}")
                return RedirectResponse(url=download_link, status_code=302)
            else:
                raise HTTPException(status_code=404, detail="Не удалось получить доступ к файлу")
        
        else:
            # ❌ Файла нет на S3 - запускаем ленивую загрузку
            logger.info(f"🚀 Запускаем ленивую загрузку для {uuid_token}")
            
            # Импортируем наш новый модуль
            from recording_downloader import RecordingDownloader
            
            downloader = RecordingDownloader()
            unique_id = call_info['unique_id']
            
            # Запускаем точечную загрузку
            download_result = await downloader.download_single_recording(unique_id)
            
            if download_result['success']:
                # Загрузка успешна - отдаем файл
                object_key = download_result['s3_object_key']
                download_link = s3_client.generate_download_link(object_key, 3600)
                
                if download_link:
                    logger.info(f"✅ Ленивая загрузка завершена: {uuid_token}")
                    return RedirectResponse(url=download_link, status_code=302)
                else:
                    raise HTTPException(status_code=500, detail="Файл загружен, но недоступен")
            else:
                # Ошибка загрузки
                error_msg = download_result.get('error_message', 'Неизвестная ошибка')
                logger.error(f"❌ Ленивая загрузка не удалась для {uuid_token}: {error_msg}")
                raise HTTPException(status_code=404, detail=f"Не удалось загрузить файл: {error_msg}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка доступа к файлу по токену: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка доступа к файлу: {str(e)}")

@app.get("/recordings/download/{uuid_token}")
async def get_download_link_by_token(
    uuid_token: str,
    expires_in: int = Query(3600, description="Время жизни ссылки в секундах")
):
    """Генерация временной ссылки для скачивания записи по UUID токену (API метод)"""
    if not s3_client:
        raise HTTPException(status_code=503, detail="S3 интеграция недоступна")
    
    if not DB_AVAILABLE:
        raise HTTPException(status_code=503, detail="База данных недоступна")
    
    try:
        # Получаем информацию о записи по UUID токену
        call_info = await get_call_recording_by_token(uuid_token)
        
        if not call_info or not call_info.get('s3_object_key'):
            raise HTTPException(status_code=404, detail="Запись не найдена")
        
        object_key = call_info['s3_object_key']
        
        # Генерируем временную ссылку
        download_link = s3_client.generate_download_link(object_key, expires_in)
        
        if download_link:
            return {
                "download_url": download_link,
                "expires_in_seconds": expires_in,
                "expires_at": (datetime.now() + timedelta(seconds=expires_in)).isoformat(),
                "uuid_token": uuid_token,
                "call_id": call_info.get('unique_id'),
                "enterprise_id": call_info.get('enterprise_id'),
                "recording_duration": call_info.get('recording_duration'),
                "call_duration": call_info.get('duration'),
                "call_start_time": call_info.get('start_time').isoformat() if call_info.get('start_time') else None,
                "phone_number": call_info.get('phone_number'),
                "object_key": object_key
            }
        else:
            raise HTTPException(status_code=404, detail="Не удалось сгенерировать ссылку")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка генерации ссылки по токену: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации ссылки: {str(e)}")

@app.post("/recordings/upload")
async def upload_recording(request: UploadRequest, background_tasks: BackgroundTasks):
    """Загрузка записи разговора в хранилище"""
    if not s3_client:
        raise HTTPException(status_code=503, detail="S3 интеграция недоступна")
    
    # Проверка существования файла
    if not os.path.exists(request.local_file_path):
        raise HTTPException(status_code=404, detail=f"Файл не найден: {request.local_file_path}")
    
    try:
        # 🔄 НОВАЯ ЛОГИКА: Получаем UUID из БД вместо генерации нового
        existing_call_info = await get_call_recording_info(request.call_unique_id)
        
        if not existing_call_info or not existing_call_info.get('uuid_token'):
            raise HTTPException(status_code=404, detail=f"UUID токен не найден для звонка {request.call_unique_id}. Сначала должно быть создано hangup событие.")
        
        existing_uuid = existing_call_info['uuid_token']
        existing_call_url = existing_call_info['call_url']
        
        logger.info(f"📋 Используем существующий UUID: {existing_uuid} для {request.call_unique_id}")
        
        # Загружаем файл в S3 с существующим UUID
        from recording_downloader import RecordingDownloader
        downloader = RecordingDownloader()
        
        # Используем метод загрузки с существующим UUID
        enterprise_number = request.enterprise_number
        name2 = s3_client._get_enterprise_name2(enterprise_number)
        
        if not name2:
            raise HTTPException(status_code=400, detail=f"Не найден name2 для предприятия {enterprise_number}")
        
        # Конвертируем файл если нужно
        file_extension = os.path.splitext(request.local_file_path)[1].lower()
        file_to_upload = request.local_file_path
        temp_files_to_cleanup = []
        
        if file_extension == '.wav':
            logger.info(f"Конвертируем WAV файл в MP3: {request.local_file_path}")
            mp3_file_path = s3_client._convert_wav_to_mp3(request.local_file_path)
            
            if mp3_file_path != request.local_file_path:
                file_to_upload = mp3_file_path
                file_extension = '.mp3'
                temp_files_to_cleanup.append(mp3_file_path)
                logger.info(f"Конвертация завершена: {mp3_file_path}")
            else:
                logger.warning(f"Конвертация не удалась, загружаем оригинальный WAV файл")
        
        # Получаем длительность
        recording_duration = s3_client.get_audio_duration(file_to_upload)
        if recording_duration is None:
            recording_duration = 0
        
        # Формируем путь S3 с существующим UUID
        call_date = request.call_date or datetime.now()
        object_key = f"CallRecords/{name2}/{call_date.year}/{call_date.month:02d}/{existing_uuid}.mp3"
        
        try:
            # Загружаем в S3
            s3_client.s3_client.upload_file(
                file_to_upload,
                s3_client.bucket_name,
                object_key,
                ExtraArgs={
                    'Metadata': {
                        'enterprise-number': enterprise_number,
                        'call-unique-id': request.call_unique_id,
                        'upload-timestamp': datetime.utcnow().isoformat(),
                        'uuid-token': existing_uuid
                    },
                    'ContentType': 'audio/mpeg'
                }
            )
            
            logger.info(f"✅ Файл загружен в S3: {object_key}")
            
            # Сохраняем информацию в БД
            if DB_AVAILABLE:
                db_success = await update_call_recording_info(
                    call_unique_id=request.call_unique_id,
                    call_url=existing_call_url,
                    s3_object_key=object_key,
                    uuid_token=existing_uuid,
                    recording_duration=recording_duration
                )
                
                if not db_success:
                    logger.warning(f"Не удалось сохранить информацию о записи в БД для звонка {request.call_unique_id}")
            
            # Очистка временных файлов
            for temp_file in temp_files_to_cleanup:
                background_tasks.add_task(cleanup_local_file, temp_file)
            
            # Опционально: удаляем локальный файл в фоне
            background_tasks.add_task(cleanup_local_file, request.local_file_path)
            
            # Генерируем временную ссылку для ответа
            file_url = s3_client.generate_download_link(object_key, 3600)
            
            return {
                "success": True,
                "public_url": existing_call_url,  # Безопасная ссылка с существующим UUID токеном
                "s3_file_url": file_url,          # Прямая ссылка на S3 (для отладки)
                "s3_object_key": object_key,
                "uuid_token": existing_uuid,
                "recording_duration": recording_duration,
                "enterprise_number": request.enterprise_number,
                "call_unique_id": request.call_unique_id,
                "upload_time": datetime.now().isoformat(),
                "db_saved": DB_AVAILABLE
            }
            
        except Exception as upload_error:
                         # Очистка временных файлов при ошибке
             for temp_file in temp_files_to_cleanup:
                 if os.path.exists(temp_file):
                     os.remove(temp_file)
             raise HTTPException(status_code=500, detail=f"Ошибка загрузки в S3: {str(upload_error)}")
            
    except Exception as e:
        logger.error(f"Ошибка загрузки записи: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки записи: {str(e)}")

@app.delete("/recordings/cleanup")
async def cleanup_old_recordings(days_to_keep: int = Query(90, description="Количество дней для хранения")):
    """Удаление старых записей"""
    if not s3_client:
        raise HTTPException(status_code=503, detail="S3 интеграция недоступна")
    
    try:
        deleted_count = s3_client.delete_old_recordings(days_to_keep)
        
        return {
            "success": True,
            "deleted_count": deleted_count,
            "days_to_keep": days_to_keep,
            "cleanup_time": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Ошибка очистки старых записей: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка очистки: {str(e)}")

async def cleanup_local_file(file_path: str):
    """Фоновая задача удаления локального файла после загрузки в S3"""
    try:
        await asyncio.sleep(5)  # Небольшая задержка
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Удален локальный файл: {file_path}")
    except Exception as e:
        logger.error(f"Ошибка удаления локального файла {file_path}: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8012) 