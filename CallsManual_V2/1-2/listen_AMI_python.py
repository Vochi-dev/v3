#!/usr/bin/python3
# -*- coding: utf-8 -*-

import asyncio
import time
import re          # Ркгулярные выражения
import platform    # For getting the operating system name
import subprocess  # For executing a shell command   https://sky.pro/wiki/python/vyzov-i-obrabotka-skripta-shell-v-python-praktiki-i-oshibki/
from manager import Manager
import threading
import multiprocessing
import queue            # https://docs-python.ru/standart-library/modul-queue-python/obekty-ocheredi-modulja-queue/
import json
import requests
from requests.models import Response
import pickle
from datetime import datetime
import sqlite3   # https://pythonru.com/biblioteki/vstavka-dannyh-v-tablicu-sqlite-v-python
                 # https://habr.com/ru/articles/754400/
from enum import Enum
import sys
import traceback
import logging
import pyping
#import configparser # для парсинга файлов конфигурации #
#https://habr.com/ru/articles/485236/

# 05.11.2024 доработка скрипта для отправки событий на сторонний сервис
# 17.01.2025 МОДЕРНИЗАЦИЯ: добавлены события BridgeCreate, BridgeLeave, BridgeDestroy, NewCallerid в AlternativeAPIlogs
# 23.09.2025 ОПТИМИЗАЦИЯ: добавлена фильтрация избыточных событий для звонков, инициированных из внешней системы
# https://webhook.site

# ✅ ОПТИМИЗАЦИЯ: Функции фильтрации событий для внешних звонков (паттерн 1-2)
# Глобальный счетчик для отслеживания bridge событий внешних звонков
_external_bridge_counter = {}

def is_external_initiated_call(message):
    """Определяет, является ли звонок инициированным из внешней системы"""
    global _external_call_active
    
    # Если флаг уже активен - это внешний звонок
    if _external_call_active:
        return True
        
    # Проверяем контекст (если есть)
    if hasattr(message, 'Context'):
        if message.Context in ('web-originate', 'set-extcall-callerid'):
            _external_call_active = True  # Активируем флаг немедленно
            return True
    # Проверяем канал
    if hasattr(message, 'Channel'):
        if message.Channel and '@web-originate' in message.Channel:
            _external_call_active = True  # Активируем флаг немедленно
            return True
    return False

def should_filter_bridge_event(message):
    """Определяет, нужно ли фильтровать bridge-событие для внешнего звонка"""
    if hasattr(message, 'Channel'):
        channel = message.Channel or ""
        
        # Для внешних звонков фильтруем ВСЕ Local каналы
        if channel.startswith('Local/'):
            # Проверяем, является ли это внешним звонком по каналу
            if '@web-originate' in channel or '@inoffice' in channel:
                return True  # Фильтруем промежуточные Local каналы
        
        # Оставляем только SIP каналы (реальные соединения)
        if channel.startswith('SIP/'):
            return False
    
    # Если это не внешний звонок - не фильтруем (отправляем как обычно)
    return False

def should_filter_new_callerid_event(message):
    """Определяет, нужно ли фильтровать new_callerid событие для внешнего звонка"""
    # Для new_callerid событий проверяем контекст и канал
    if hasattr(message, 'Context') and hasattr(message, 'Channel'):
        # Если контекст inoffice или канал содержит @inoffice - это внешний звонок
        if message.Context == 'inoffice' or '@inoffice' in (message.Channel or ''):
            return True  # Фильтруем промежуточные события inoffice
        # Оставляем только события from-out-office (реальный внешний канал)
        if message.Context == 'from-out-office':
            return False  # Не фильтруем - это нужное событие
    # Если это не внешний звонок - не фильтруем
    if not is_external_initiated_call(message):
        return False
    # Все остальные new_callerid события фильтруем
    return True

# Глобальные счетчики для внешних звонков
_bridge_create_counter = 0
_bridge_destroy_counter = 0
_hangup_sent = False
_event_counter = 0  # Счетчик всех событий внешнего звонка

def should_filter_bridge_create_destroy_event(message):
    """Определяет, нужно ли фильтровать bridge_create/bridge_destroy события"""
    global _bridge_create_counter, _bridge_destroy_counter, _external_call_active
    
    # Применяем фильтрацию только для внешних звонков
    if not _external_call_active and not is_external_initiated_call(message):
        return False
    
    # Для максимального соответствия обычному звонку: 1 create + 1 destroy
    if message.Event == 'BridgeCreate':
        _bridge_create_counter += 1
        if _bridge_create_counter <= 1:  # Оставляем только первый
            return False
        else:
            return True  # Фильтруем остальные
    elif message.Event == 'BridgeDestroy':
        _bridge_destroy_counter += 1
        # Оставляем только ОДИН destroy (первый попавшийся)
        if _bridge_destroy_counter <= 1:
            return False  # Первый destroy - не фильтруем
        else:
            return True  # Все остальные - фильтруем
    
    return False

def reset_bridge_counter(uniqueid):
    """Сбросить счетчик bridge событий для звонка (только очистка локальных данных)"""
    if uniqueid in _external_bridge_counter:
        del _external_bridge_counter[uniqueid]
    # Глобальные флаги НЕ сбрасываем - это делает таймер

# Глобальная переменная для отслеживания состояния внешнего звонка
_external_call_active = False

def add_external_marker_to_body(body, data):
    """Добавляет маркер внешней инициации к Body события"""
    global _external_call_active, _bridge_create_counter, _event_counter
    
    # Определяем, является ли звонок внешним по наличию определенных маркеров
    is_external = False
    
    # Проверяем по данным события
    if hasattr(data, 'Channel') and data.Channel:
        if '@web-originate' in data.Channel or '@inoffice' in data.Channel:
            is_external = True
            _external_call_active = True  # Активируем флаг внешнего звонка
    
    # Проверяем по контексту
    if hasattr(data, 'Context') and data.Context:
        if data.Context in ('web-originate', 'set-extcall-callerid', 'inoffice'):
            is_external = True
            _external_call_active = True  # Активируем флаг внешнего звонка
    
    # Проверяем активные счетчики (если они больше 0, значит идет внешний звонок)
    if _bridge_create_counter > 0 or _external_call_active:
        is_external = True
    
    # Добавляем маркер в Body если это внешний звонок
    if is_external and body:
        body['ExternalInitiated'] = True
        _event_counter += 1
        
        # После 10 событий сбрасываем флаги (звонок завершен)
        if _event_counter >= 10:
            _external_call_active = False
            _event_counter = 0
    
    return body

def should_limit_bridge_events(message, event_type):
    """Ограничивает количество bridge событий для внешних звонков"""
    if not is_external_initiated_call(message):
        return False
    if not hasattr(message, 'Uniqueid'):
        return False
    uniqueid = message.Uniqueid
    # Инициализируем счетчик для данного звонка
    if uniqueid not in _external_bridge_counter:
        _external_bridge_counter[uniqueid] = {'bridge': 0, 'bridge_leave': 0}
    # Увеличиваем счетчик
    _external_bridge_counter[uniqueid][event_type] += 1
    # Разрешаем только первые 2 события каждого типа (для двух каналов)
    if _external_bridge_counter[uniqueid][event_type] <= 2:
        return False  # Не фильтруем
    else:
        return True   # Фильтруем лишние события

# переменные для стороннего сервиса:
apiAlternativeURL = 'https://bot.vochi.by/'
apiAlternative = 1   # 0 - выключено,  1 - включено

# переменные для второго стороннего сервиса:
apiAlternative2URL = 'https://manager.1akb.by/'
apiAlternative2 = 0   # 0 - выключено,  1 - включено

# try https://metanit.com/python/tutorial/2.11.php
# for https://metanit.com/python/tutorial/2.7.php
# перменные https://metanit.com/python/tutorial/2.2.php
# class https://metanit.com/python/tutorial/7.1.php

# Настройка логера в SysLog

#my_logger = logging.basicConfig(level=logging.INFO,
#filename="/var/log/asterisk/ListenAMI_Py.log",filemode="w")
#logging.basicConfig(level=logging.INFO, filename="/var/log/asterisk/ListenAMI_Py.log",filemode="w",
#                    format="%(asctime)s %(levelname)s %(message)s")

# Создаем очереди
q = queue.Queue(10000)                      # очередь для POST запросов на crm.vochi.by
DbQueue = queue.Queue(10000)                # очередь для записи в БД

if apiAlternative == 1:
    apiAlternativeQueue = queue.Queue(10000)    # очередь для POST запросов на сторонний сервис

if apiAlternative2 == 1:
    apiAlternative2Queue = queue.Queue(10000)    # очередь для POST запросов на второй сторонний сервис

# для хранения состояния и запуска изменений доступности интернет # 0 - подключено 1 - отключено
pingState=3

# переменная для таймера потока setTime_thrad
setTime_thread_timer = None




class ScriptEvent:
    
    def __init__(self, name, caption):
        self.Name = name
        self.Caption = caption
        self.DateTime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class AMIEvent:
    Event = ''
    DateTime = ''
    Event = ''
    Uniqueid = ''
    CallerIDNum = ''
    CallerIDName = ''
    Exten = ''
    Extension = ''
    Context = ''
    Channel = ''
    DestChannel = ''
    ConnectedLineNum = ''
    ConnectedLineName = ''
    BridgeUniqueid = ''
    Application = ''
    AppData = ''
    CIDCallingPres = ''
    DestChannelState = ''
    DestChannelStateDesc = ''
    DestCallerIDNum = ''
    DestCallerIDName = ''
    DestConnectedLineNum = ''
    DestConnectedLineName = ''
    DestAccountCode = ''
    DestContext = ''
    DestExten = ''
    DestPriority = ''
    DestUniqueid = ''
    DialString = ''
    DialStatus = ''
    BridgeType = ''
    BridgeTechnology = ''
    BridgeCreator = ''
    BridgeName = ''
    BridgeNumChannels = ''
    Cause = ''
    Causetxt = ''
    Priority = ''
    ChannelState = ''
    ChannelStateDesc = ''
    Privilege = ''
    AccountCode = ''
    Value = ''
    Variable = ''

    def __init__(self, Event):
        self.Event = Event
        self.DateTime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class APIEvent:

    def __init__(self, event, request, Uniqueid, status, response, responseTime):
        self.event = event
        self.Uniqueid = Uniqueid
        self.request = request
        self.status = status
        self.response = response
        self.responseTime = responseTime
        self.DateTime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class DB_Type(Enum):
    Script_Event = 1
    AMI_Event = 2
    API_Event = 3
    AlternativeAPI_Event = 4
    Alternative2API_Event = 5


class DbItem:
    def __init__(self, type: DB_Type, data):
        self.type = type
        self.data = data


class Channel_class:
    Uniqueid = ''
    Context = ''
    Channel = ''
    CallUniqueid = ''
    CallType = ''
     

    def __init__(self, Uniqueid, Context, Channel):
        self.Uniqueid = Uniqueid
        self.Context = Context
        self.Channel = Channel

    def __str__(self):
        return ' '+self.Uniqueid+' '+self.Context+' '+self.Channel+' '+self.CallType+' '+self.CallUniqueid

class simle_call:
    Uniqueid = ''
    CallerIDNum = ''
    CallerIDName = ''
    Trunk = ''

#hangup: {"Trunk": "0001368", "Phone": "+375293193330", "EndTime": "2024-07-15 22:18:43", "Token": "375291448457", "StartTime": "2024-07-15 22:18:41", "CallStatus": "0", "CallType": 0, "DateReceived": "2024-07-15 22:18:41", "Extensions": [""], "UniqueId": "1721071121.46"}
#start:{"Trunk": "0001368", "Phone": "+375293193330", "CallType": 0, "UniqueId": "1721071129.51", "Token": "375291448457"}
# dial:{"Trunk": "0001368", "Phone": "+375293193330", "ExtTrunk": "", "ExtPhone": "+375293193330", "Token": "375291448457", "CallType": 0, "Extensions": ["152&151&153"], "UniqueId": "1721071129.51"}
# bridge: {"Channel": "SIP/151-00000002", "Exten": "375296254070", "CallerIDNum": "151", "Token": "375291448457", "CallerIDName": "151", "ConnectedLineNum": "<unknown>", "ConnectedLineName": "<unknown>", "UniqueId": "1720589560.3"}

    def __init__(self, Uniqueid, CallerIDNum):
        self.Uniqueid = Uniqueid
        self.CallerIDNum = CallerIDNum
        self.DateTimeStart = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    

class Call_class:
    Uniqueid = ''
    StartChannel = ''
    DateTimeStart = ''
    Context_start = ''
    Trunk = ''
    CallType = ''
    DialStatus = ''
    PhoneTo = ''    
    LastBridgeNum = ''
    SoftHangupRequest = 0  # было событие SoftHangupRequest 0- нет пометки 1- есть пометка
    DialNum = []
    DialSended = 0          # отметка было ли отправлено событие на hedServer
    #Channel_list = [] # список каналов
                             #BridgeList = [] # список бриджей
                             #DialList = [] # список вызовов

    def __init__(self, Uniqueid, Channel):
        self.Uniqueid = Uniqueid
        self.StartChannel = Channel
        self.DateTimeStart = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def __str__(self): 
        DialNum_string = ''
        for el in self.DialNum:
            DialNum_string += ' '+str(el)

        return ' '+self.Uniqueid+' '+self.StartChannel+' '+self.DateTimeStart+' '+self.Context_start+' '+self.Trunk+' '+self.CallType+' '+self.DialStatus+' '+self.PhoneTo+' '+self.LastBridgeNum+' '+str(self.SoftHangupRequest)+' '+DialNum_string+' '+str(self.DialSended)





# Флаг для завершения скрипта
exiting = False

# путь к БД
DB_Path = ''

# данные для доступа к AMI
ip = '127.0.0.1'
username = 'amilisten'
secret = 'PGnlZsDZEB4qFNqu'

# дата страта скрипта для создания нового файа БД
dateStart = datetime.now().strftime("%Y-%m-%d")

#logging.info('dateStart: ' + dateStart)

# API адрес CRM
#url = 'http://crm.vochi.by:5000/api/callevent/'
url = 'https://crm.vochi.by/api/callevent/'



# Unit ID
UnitID_path = '/root/id.conf'
UnitID = ''

# получаем Unit ID
try:
    unitIDfile = open(UnitID_path, 'r')
    tempUnitID = unitIDfile.readline()
    f = filter(str.isdecimal, tempUnitID)
    UnitID = "".join(f)
    DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Get UnitID', UnitID)))

except:
    print('Error Get UnitID from ' + UnitID_path)



# определяем операцилнную систему
os_name = ''.join(platform.system())
os_version = ''.join(platform.version())
os_release = ''.join(platform.release())
os_architecture = ''.join(platform.architecture())
os_processor = ''.join(platform.processor())
os_python_build = ''.join(platform.python_build())



print()
print('###      Run Vochi Listen AMI python script by Evgeny Popichev 2024      ###')
print()
print('Platform: ' + os_name + ' ' + os_version + ' ' + os_architecture + ' ' + os_release)
print('Processor: ' + os_processor+'    Python build: ' + os_python_build)
print('UnitID: '+UnitID+'    DateTimeStart: ' + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
print()

if datetime.now().strftime("%Y-%m-%d") == '1970-01-01':
    print('Wati 30 sec: ')
    #logging.info('Wati 25 sec ')
    for i in reversed(range(0, 30)):
        time.sleep(1)  #  time.sleep(5.5) # Pause 5.5 seconds
        print(i)

    print('New DateTimeStart: ' + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    #logging.info('New DateTimeStart: ' + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

print('Ready')

# Список звонков
Call_List = []

# Список активных каналов
Channel_list = []






def GetDBPath(OSname):
    global dateStart

    dateStart = datetime.now().strftime("%Y-%m-%d")
       

    if (OSname == 'Windows'):
        DB_Path = 'd:\Temp\python_Vochi_' + dateStart + '.db'
        #logging.info('New DB_Path' + DB_Path)
        return DB_Path

    elif (OSname == 'Linux'):
        DB_Path = '/var/log/asterisk/Listen_AMI_' + dateStart + '.db'
        #logging.info('New DB_Path' + DB_Path)
        return DB_Path


DB_Path = GetDBPath(os_name)


# для работы с БД SQlite
def createDB(DBPath):
    global connection

    # https://habr.com/ru/articles/754400/
    connection = sqlite3.connect(DB_Path)
    cursor = connection.cursor()


    # Создаем таблицу ScriptEvents
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ScriptEvents (
    id INTEGER PRIMARY KEY,
    Event,
    DateTime,
    Caption
    )
    ''')

    # Создаем таблицу APIlogs
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS APIlogs (
    id INTEGER PRIMARY KEY,
    DateTime,
    event,
    Uniqueid, 
    request, 
    status,
    response,
    responseTime    
    )
    ''')

     # Создаем таблицу AlternativeAPIlogs
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS AlternativeAPIlogs (
    id INTEGER PRIMARY KEY,
    DateTime,
    event,
    Uniqueid, 
    request, 
    status,
    response,
    responseTime    
    )
    ''')

     # Создаем таблицу Alternative2APIlogs
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS Alternative2APIlogs (
    id INTEGER PRIMARY KEY,
    DateTime,
    event,
    Uniqueid, 
    request, 
    status,
    response,
    responseTime    
    )
    ''')

    # Создаем таблицу AMIEvents
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS AMIEventLog (
    id INTEGER PRIMARY KEY,
    DateTime TEXT NOT NULL,
    Event TEXT NOT NULL,
    Uniqueid TEXT NOT NULL,
    CallerIDNum,
    CallerIDName,
    Exten,
    Extension,
    Context,
    Channel,
    DestChannel,
    ConnectedLineNum,
    ConnectedLineName,
    BridgeUniqueid,
    Application,
    AppData,
    CIDCallingPres,
    DestChannelState,
    DestChannelStateDesc,
    DestCallerIDNum,
    DestCallerIDName,
    DestConnectedLineNum,
    DestConnectedLineName,
    DestAccountCode,
    DestContext,
    DestExten,
    DestPriority,
    DestUniqueid,
    DialString,
    DialStatus,
    BridgeType,
    BridgeTechnology,
    BridgeCreator,
    BridgeName,
    BridgeNumChannels,
    Cause,
    Causetxt,
    Priority,
    ChannelState,
    ChannelStateDesc,
    Privilege,
    AccountCode,
    Value,
    Variable
    )
    ''')
    

    # Сохраняем изменения и закрываем соединение
    connection.commit()

    return cursor



def Write_toDB(type: DB_Type, cur, connection, data):
    #print('run Write_toDB')
    #print('api_data:'+repr(data))


    if type == DB_Type.API_Event:
        
        try:
             cur.execute('INSERT INTO APIlogs (DateTime, event, Uniqueid,  request, status, response, responseTime) VALUES (?, ?, ?, ?, ?, ?, ?)', (data.DateTime, data.event, data.Uniqueid, data.request, data.status, data.response, data.responseTime))
             connection.commit()
                     
        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

                # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', 'Exception type: ' + ex_type.__name__ + '    /r/n' + 'Exception message: ' + ex_value + '    /r/n' + 'Stack trace: ' + stack_trace)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###   Write_toDB API_Event Error: %s" % stack_trace)

    elif type == DB_Type.AlternativeAPI_Event: 

        try:
             cur.execute('INSERT INTO AlternativeAPIlogs (DateTime, event, Uniqueid,  request, status, response, responseTime) VALUES (?, ?, ?, ?, ?, ?, ?)', (data.DateTime, data.event, data.Uniqueid, data.request, data.status, data.response, data.responseTime))
             connection.commit()
                     
        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

                # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', 'Exception type: ' + ex_type.__name__ + '    /r/n' + 'Exception message: ' + ex_value + '    /r/n' + 'Stack trace: ' + stack_trace)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###   Write_toDB API_Event Error: %s" % stack_trace)

    elif type == DB_Type.Alternative2API_Event: 

        try:
             cur.execute('INSERT INTO Alternative2APIlogs (DateTime, event, Uniqueid,  request, status, response, responseTime) VALUES (?, ?, ?, ?, ?, ?, ?)', (data.DateTime, data.event, data.Uniqueid, data.request, data.status, data.response, data.responseTime))
             connection.commit()
                     
        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

                # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', 'Exception type: ' + ex_type.__name__ + '    /r/n' + 'Exception message: ' + ex_value + '    /r/n' + 'Stack trace: ' + stack_trace)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###   Write_toDB Alternative2API_Event Error: %s" % stack_trace)       


                


    elif type == DB_Type.Script_Event:        
        try:
             cur.execute('INSERT INTO ScriptEvents (Event, DateTime, Caption) VALUES (?, ?, ?)', (data.Name, data.DateTime, data.Caption))
             connection.commit()

        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

                # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', 'Exception type: ' + ex_type.__name__ + '    /r/n' + 'Exception message: ' + ex_value + '    /r/n' + 'Stack trace: ' + stack_trace)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###   Write_toDB Script_Event Error: %s" % stack_trace)



    elif type == DB_Type.AMI_Event:
        
        try:
             cur.execute("INSERT INTO AMIEventLog (Event, DateTime, Uniqueid, CallerIDNum, CallerIDName, Exten, Extension, Context, Channel, DestChannel, ConnectedLineNum, ConnectedLineName, BridgeUniqueid, Application, AppData, CIDCallingPres, DestChannelState, DestChannelStateDesc, DestCallerIDNum, DestCallerIDName, DestConnectedLineNum, DestConnectedLineName, DestAccountCode, DestContext, DestExten, DestPriority, DestUniqueid, DialString, DialStatus, BridgeType, BridgeTechnology, BridgeCreator, BridgeName, BridgeNumChannels, Cause, Causetxt, Priority, ChannelState, ChannelStateDesc, Privilege, AccountCode, Value, Variable) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                            (data.Event,
                               data.DateTime,
                               data.Uniqueid,
                               data.CallerIDNum,
                               data.CallerIDName,
                               data.Exten,
                               data.Extension,
                               data.Context,
                               data.Channel,
                               data.DestChannel,
                               data.ConnectedLineNum,
                               data.ConnectedLineName,
                               data.BridgeUniqueid,
                               data.Application,
                               data.AppData,
                               data.CIDCallingPres,
                               data.DestChannelState,
                               data.DestChannelStateDesc,
                               data.DestCallerIDNum,
                               data.DestCallerIDName,
                               data.DestConnectedLineNum,
                               data.DestConnectedLineName,
                               data.DestAccountCode,
                               data.DestContext,
                               data.DestExten,
                               data.DestPriority,
                               data.DestUniqueid,
                               data.DialString,
                               data.DialStatus,
                               data.BridgeType,
                               data.BridgeTechnology,
                               data.BridgeCreator,
                               data.BridgeName,
                               data.BridgeNumChannels,
                               data.Cause,
                               data.Causetxt,
                               data.Priority,
                               data.ChannelState,
                               data.ChannelStateDesc,
                               data.Privilege,
                               data.AccountCode,
                               data.Value,
                               data.Variable))
             connection.commit()

        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

                # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', 'Exception type: ' + ex_type.__name__ + '    /r/n' + 'Exception message: ' + ex_value + '    /r/n' + 'Stack trace: ' + stack_trace)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###  Write_toDB Script_Event Error: %s" % stack_trace)



# Создаем подключение к базе данных (файл будет создан)
cursor = createDB(DB_Path)
             




# добавление в очередь на запись в БД
DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('RUN', 'Script RUN' + ' UnitID: ' + UnitID + '  Platform: ' + os_name + ' ' + os_version + ' ' + os_architecture + ' ' + os_release + '   Processor: ' + os_processor + '   Python build: ' + os_python_build + ' DateTimeStart: ' + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))))
  
# Запись в БД состояния альтернативной интеграции
if apiAlternative == 1:
    DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('apiAlternative', 'Enabled')))
    DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('apiAlternative', 'URL: ' + apiAlternativeURL)))
    
else:
    DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('apiAlternative', 'Disabled')))

# Запись в БД состояния второй альтернативной интеграции
if apiAlternative2 == 1:
    DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('apiAlternative2', 'Enabled')))
    DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('apiAlternative2', 'URL: ' + apiAlternative2URL)))
    
else:
    DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('apiAlternative2', 'Disabled')))


def changeState(value):
    global DbQueue, pingState

    if pingState != value:
        state_old = 'Connected' if pingState == 0 else 'Disconnected'
        state_new = 'Connected' if value == 0 else 'Disconnected'
        #print('Connection state: '+ state_old +' -> '+ state_new)
        pingState = value
       

        DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Internet Available', 'Change State ' + 'Connected' if pingState == 0 else 'Change State ' + 'Disconnected')))
        SipAddProvidersEdit()   # изменение фала /etc/asterisk/sip_addproviders.conf для решения проблемы
                                # резолва DNS при недоступном интернет
       

# проверка доступности интеренет через пинг нескольких хостов Google и Hoster.by
def ping():
    global DbQueue, pingState

    # https://github.com/certator/pyping/tree/master

    # 0 - подключено 1 - отключено

    google_ping = pyping.ping('8.8.8.8', 200, 1)    
    hosterby_ping = pyping.ping('178.172.160.100', 200, 1)

    if google_ping.ret_code == 0 and hosterby_ping.ret_code == 0:
        changeState(0)
    else:
        changeState(1)

    #DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Internet Available', 'State ' + ' Connected' if pingState == 0 else ' Disconnected')))

        #print('ping(self) pingState: '+repr(self.pingState))
    pass


# коментирование и раскоментирование sip-линий в
# /etc/asterisk/sip_addproviders.conf при отключении интернет
def SipAddProvidersEdit():
    global pingState, DbQueue


    providers_list = [] # список для провайдеров для обработки
    providers_list_pruf = [] # список для записи в файл
        
    # читаем файл
    with open("/etc/asterisk/sip_addproviders.conf", "r") as file:
        sip_providers_lines = file.readlines()

    # перебираем список строк получаем список провайдеров
    for sip_line in sip_providers_lines:                                    # Если нач нется с "заголовка или раздела"
        if sip_line[0:2] == ';[' or sip_line[0:1] == '[':
            prov = []
            if sip_line[0:2] == ';[':       # убираем коментарий
                sip_line = sip_line[1:]
            prov.append(sip_line)
            providers_list.append(prov)
            
        elif re.search(r'.', sip_line[0:1]):                                # если начинается с любого символа
            if sip_line[0:1] == ';':       # убираем коментарий
                sip_line = sip_line[1:]
            providers_list[-1].append(sip_line)



    #res = json.dumps(providers_list)
    #print('---------------------------------')
    #print(res)
    #print('---------------------------------')

    # перебираем список и добавляем ";" коментарий
    if pingState == 1:      # отключено -> нужно закоментировать
          
        for provider in providers_list:
            # поиск подходящей строки (должна начинаться с "[" и заканчиваться
            # "]"
            # значение внутри не должно быть = 3 цифры, 7 цифр начинающихся с
            # "000")
            match_xxx = re.search(r'[[]\d\d\d[\]]', provider[0])
            if match_xxx:
                #print('XXX')
                pass

            else:
                match_000xxxx = re.search(r'[[][0][0][0]\d\d\d\d[\]]', provider[0])
                if match_000xxxx:
                    #print('000XXXX')
                    pass

                else:       # нужно комментировать
                    new_provider = []
                    for p_line in provider:
                        new_provider.append(';' + p_line)

                    provider = new_provider.copy()  # копирование вновь созданного списка (new_provider) в provider
                                       

            providers_list_pruf.append(provider)

    else:
        providers_list_pruf = providers_list.copy()




    # записываем спсок в файл
    with open("/etc/asterisk/sip_addproviders.conf", "w") as file:
        for provider in providers_list_pruf:
            for line in provider:
                file.write(line)
                pass
            file.write('\n')

    subprocess.run(['asterisk', '-rx', 'sip reload'])
    DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('sip_addproviders.conf', 'Updated')))

 
                        

                        
                        
            








manager = Manager(loop=asyncio.get_event_loop(),
                  host=ip,
                  username=username,
                  secret=secret)



#@manager.register_event('*') # Register all events
#async def ami_callback(manager, message):
#   if message.Event not in ('Newexten'):
#        print(message)

#   #if message.Event == 'VarSet':
#       #if message.Variable == 'Dial_num':
#       #print(message)


# Смотрим событие Newchannel c необходимыми полями
@manager.register_event('Newchannel')
def NewChannel(manager, message):
    global DbQueue
    
    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Extension = message.Extension
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid
    
    # # Создаем объект Channel_class
    # chn = Channel_class(message.Uniqueid, message.Context, message.Channel)

    # # добавляем в список каналов
    # Channel_list.append(chn)

    

    
    # #DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Newchannel', res)))

    # # указываем контексты для звонка
    # if (message.Context == 'from-out-office'):
    #     if message.CallerIDNum != '<unknown>':
    #         # создаем Call
    #         call = Call_class(Uniqueid = message.Uniqueid, Channel = message.Channel)
    #         call.CallType = 'in'
    #         call.Context_start = message.Context
    #         call.Trunk = message.Exten

    #         # добавляем в список вызовов
    #         Call_List.append(call)            

    #         # изменяем канал в списке каналов
    #         if chn in Channel_list:
    #             i = Channel_list.index(chn)      # получаем индекс элемента в списке
    #             chn.CallType = 'in'
    #             chn.CallUniqueid = message.Uniqueid
    #             Channel_list[i] = chn

    #         # добавляем в очередь для отправки события
    #         q.put(amiEvent)
    
    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))

    # # для отладки    
    # print('Channel_list---------------------------------')
    # print(*Channel_list, sep='\n')
    # print('Call_List------------------------------------')
    # print(*Call_List, sep='\n')
    # print('---------------------------------------------')
    


# Смотрим событие Newstate c необходимыми полями
@manager.register_event('Newstate')
def Newstate(manager, message):
    global DbQueue


    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid


   # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))



# Смотрим событие Newexten c необходимыми полями
#@manager.register_event('Newexten')
#def Newexten(manager, message):
#    global DbQueue

# # создаем объект
#    amiEvent = AMIEvent(Event = message.Event)

#    amiEvent.Privilege = message.Privilege
#    amiEvent.Channel = message.Channel
#    amiEvent.ChannelState = message.ChannelState
#    amiEvent.ChannelStateDesc = message.ChannelStateDesc
#    amiEvent.CallerIDNum = message.CallerIDNum
#    amiEvent.CallerIDName = message.CallerIDName
#    amiEvent.ConnectedLineNum = message.ConnectedLineNum
#    amiEvent.ConnectedLineName = message.ConnectedLineName
#    amiEvent.AccountCode = message.AccountCode
#    amiEvent.Context = message.Context
#    amiEvent.Exten = message.Exten
#    amiEvent.Priority = message.Priority
#    amiEvent.Uniqueid = message.Uniqueid
#    amiEvent.Extension = message.Extension
#    amiEvent.Application = message.Application
#    amiEvent.AppData = message.AppData
    

    
#    # добавление в очередь
#    DbQueue.put(amiEvent)


# Смотрим событие VarSet c необходимыми полями
@manager.register_event('VarSet')
def VarSet(manager, message):
    global DbQueue, q, Channel_list, apiAlternativeQueue

    if message.Variable in ('LisAMI_DialNum', 'LisAMI_PhoneTo', 'LisAMI_CallType', 'LisAMI_GetCustomerData', 'LisAMI_Trunk', 'LisAMI_CallerIdNum', 'LisAMI_EventData'):
    #if message.Variable == 'Dial_num':
        # создаем объект
        amiEvent = AMIEvent(Event = message.Event)

        amiEvent.Privilege = message.Privilege
        amiEvent.Channel = message.Channel
        amiEvent.ChannelState = message.ChannelState
        amiEvent.ChannelStateDesc = message.ChannelStateDesc
        amiEvent.CallerIDNum = message.CallerIDNum
        amiEvent.CallerIDName = message.CallerIDName
        amiEvent.ConnectedLineNum = message.ConnectedLineNum
        amiEvent.ConnectedLineName = message.ConnectedLineName
        amiEvent.AccountCode = message.AccountCode
        amiEvent.Context = message.Context
        amiEvent.Exten = message.Exten
        amiEvent.Priority = message.Priority
        amiEvent.Uniqueid = message.Uniqueid
        amiEvent.Variable = message.Variable
        amiEvent.Value = message.Value

        # добавление в очередь
        DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))

                   
        
        


        # Создаем объект Channel_class
        chn = Channel_class(message.Uniqueid, message.Context, message.Channel)
    
        # обработка устанавливаемых значений переменных 
        # if message.Variable == 'LisAMI_CallType':
        #     # поиск в списке вызовов, если нету создаем и добавляем в список
        #     call_findet = 0
        #     for call in Call_List:
        #         if call.Uniqueid == message.Uniqueid:
        #             call_findet = 1

        #     if call_findet == 0:
        #         # Создаем вызов
        #         call = Call_class(Uniqueid = message.Uniqueid, Channel = message.Channel)
        #         call.CallType = message.Value
        #         # добавляем в список вызовов
        #         Call_List.append(call) 

        #     if chn in Channel_list:
        #         i = Channel_list.index(chn)      # получаем индекс элемента в списке
        #         chn.CallType = message.Value
        #         Channel_list[i] = chn

            
        # elif message.Variable == 'LisAMI_DialNum':  
        #     for call in Call_List:
        #         if call.Uniqueid == message.Uniqueid:
        #             DialNum_temp = message.Value.split(sep='&')
        #             call.DialNum = DialNum_temp



        # elif message.Variable == 'LisAMI_Trunk':  
        #     for call in Call_List:
        #         if call.Uniqueid == message.Uniqueid:
        #             call.Trunk = message.Value
            

       

        # elif message.Variable == 'LisAMI_PhoneTo':
        #     for call in Call_List:
        #         if call.Uniqueid == message.Uniqueid:
        #             call.PhoneTo = message.Value
            
        # # LisAMI_CallerIdNum
        # elif message.Variable == 'LisAMI_CallerIdNum':
        #     for call in Call_List:
        #         if call.Uniqueid == message.Uniqueid:
        #             call.CallerIDNum = message.Value


        # LisAMI_EventData
        if message.Variable == 'LisAMI_EventData':
            temp_data = message.Value.split(sep='_')

            # создаем объект
            amiEvent = None

            if temp_data[0] == 'localcallStart':
                #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${ARG1}"],"CallType":"2","Trunk":"","ExtTrunk":"","ExtPhone":""})
                #same => n,Set(LisAMI_EventData="localcall_start"&${CALLERID(num)}"&${ARG1})

                amiEvent =  AMIEvent(Event = 'localcallStart')
               
                amiEvent.Uniqueid = message.Uniqueid
                amiEvent.Phone = temp_data[1]
                amiEvent.Extensions = temp_data[2].split(sep='&')

                # Создаем вызов
                call = simle_call(Uniqueid = message.Uniqueid, CallerIDNum = amiEvent.Phone)

                # добавляем в список вызовов
                Call_List.append(call) 

                
            elif temp_data[0] == 'localcallEnd':
                call = None
                # поиск вызова в списке вызовов
                for c in Call_List:
                    if c.Uniqueid == message.Uniqueid:
                        call = c

                

                #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${DIALEDPEERNUMBER}"],"CallType":"2","DateReceived":"${CDR(start)}","StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}"})
                #exten => s,1,Set(LisAMI_EventData=localcallEnd_${CALLERID(num)}_${DIALEDPEERNUMBER}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${DIALSTATUS})
                amiEvent =  AMIEvent(Event = 'localcallEnd')

                amiEvent.Uniqueid = message.Uniqueid
                amiEvent.Phone = temp_data[1]
                amiEvent.Extensions = temp_data[2].split(sep='&')
                amiEvent.DateReceived = temp_data[3]
                amiEvent.StartTime = temp_data[4]
                amiEvent.EndTime = temp_data[5]
                amiEvent.CallStatus = temp_data[6]

                

                # удаляем из списка вызовов
                if call:
                    if call in Call_List:
                        Call_List.remove(call)

                
            elif temp_data[0] == 'incallStart':

                 

                #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","CallType":"0","Trunk":"${ARG1}"})
                # same => n,Set(LisAMI_EventData="incall_start"&${CALLERID(num)}"&${ARG1})

                amiEvent =  AMIEvent(Event = 'incallStart')

                amiEvent.Uniqueid = message.Uniqueid
                amiEvent.Phone = temp_data[1]
                amiEvent.Trunk = temp_data[2]

                # Создаем вызов
                call = simle_call(Uniqueid = message.Uniqueid, CallerIDNum = amiEvent.Phone)
                call.Trunk = amiEvent.Trunk

                # добавляем в список вызовов
                Call_List.append(call)

                
            elif temp_data[0] == 'incallDial':

                call = None
                # поиск вызова в списке вызовов
                for c in Call_List:
                    if c.Uniqueid == message.Uniqueid:
                        call = c

                # ;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${Exten}"],"CallType":"0","Trunk":"${ARG1}","ExtTrunk":"","ExtPhone":"${CALLERID(num)}"})
                #same => n,Set(LisAMI_EventData="incall_dial"&${CALLERID(num)}"&${ARG2}&${ARG1})

                amiEvent =  AMIEvent(Event = 'incallDial')

                # Проверки
                phone = None
                if temp_data[1] in ('', '<unknown>', 'Anonymous'):
                    phone = call.CallerIDNum
                
                else:
                    phone = temp_data[1]


                trunk = None
                if temp_data[3] =='':
                    trunk = call.Trunk

                else:
                    trunk = temp_data[3]

                amiEvent.Uniqueid = message.Uniqueid
                amiEvent.Phone = phone
                amiEvent.Extensions = temp_data[2].split(sep='&')
                amiEvent.Trunk = trunk



                
            elif temp_data[0] == 'incallEnd':
                call = None
                # поиск вызова в списке вызовов
                for c in Call_List:
                    if c.Uniqueid == message.Uniqueid:
                        call = c

                #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${DIALEDPEERNUMBER}"],"CallType":"2","DateReceived":"${CDR(start)}","StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})
                #exten => s,1,Set(LisAMI_EventData=incallEnd_${CALLERID(num)}_${DIALEDPEERNUMBER}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${ARG1}_${DIALSTATUS})

                amiEvent =  AMIEvent(Event = 'incallEnd')

                # Проверки
                phone = None
                if temp_data[1] in ('', '<unknown>', 'Anonymous'):
                    phone = call.CallerIDNum
                
                else:
                    phone = temp_data[1]


                trunk = None
                if temp_data[6] in ('', '<unknown>'):
                    trunk = call.Trunk

                else:
                    trunk = temp_data[6]

                amiEvent.Uniqueid = message.Uniqueid
                amiEvent.Phone = phone
                amiEvent.Extensions = temp_data[2].split(sep='&')
                amiEvent.DateReceived = temp_data[3]
                amiEvent.StartTime = temp_data[4]
                amiEvent.EndTime = temp_data[5]
                amiEvent.Trunk = trunk
                amiEvent.CallStatus = temp_data[7]


                # удаляем из списка вызовов
                if call:
                    if call in Call_List:
                        Call_List.remove(call)


            elif temp_data[0] == 'outcallDial':
                #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${ARG2}","Extensions":["${CALLERID(num)}"],"CallType":"1","Trunk":"${ARG1}","ExtTrunk":"","ExtPhone":""})
                #same => n,Set(LisAMI_EventData="outcall_dial"&${ARG2}&${CALLERID(num)}"&${ARG1})

                
                amiEvent =  AMIEvent(Event = 'outcallDial')

                amiEvent.Uniqueid = message.Uniqueid
                amiEvent.Phone = temp_data[1]
                amiEvent.Extensions = temp_data[2].split(sep='&')
                amiEvent.Trunk = temp_data[3]

                # Создаем вызов
                call = simle_call(Uniqueid = message.Uniqueid, CallerIDNum = amiEvent.Phone)
                call.Trunk = amiEvent.Trunk

                # добавляем в список вызовов
                Call_List.append(call)


            elif temp_data[0] == 'outcallEnd':

                call = None
                # поиск вызова в списке вызовов
                for c in Call_List:
                    if c.Uniqueid == message.Uniqueid:
                        call = c


                #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${PhoneTo}","Extensions":["${CALLERID(num)}"],"CallType":"1","DateReceived":"${CDR(start)}","StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})
                # exten => s,1,Set(LisAMI_EventData=outcallEnd_${PhoneTo}_${CALLERID(num)}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${ARG1}_${DIALSTATUS})

                
                amiEvent =  AMIEvent(Event = 'outcallEnd')

                # Проверки
                phone = None
                if temp_data[1] in ('', '<unknown>', 'Anonymous'):
                    phone = call.CallerIDNum
                
                else:
                    phone = temp_data[1]


                trunk = None
                if temp_data[6] in ('', '<unknown>'):
                    trunk = call.Trunk

                else:
                    trunk = temp_data[6]


                amiEvent.Uniqueid = message.Uniqueid
                amiEvent.Phone = phone
                amiEvent.Extensions = temp_data[2].split(sep='&')
                amiEvent.DateReceived = temp_data[3]
                amiEvent.StartTime = temp_data[4]
                amiEvent.EndTime = temp_data[5]
                amiEvent.Trunk = trunk
                amiEvent.CallStatus = temp_data[7]


                # удаляем из списка вызовов
                if call:
                    if call in Call_List:
                        Call_List.remove(call)
                
                


          
            if amiEvent:
                # Ставим в очередь на отправку            
                q.put(amiEvent)

                # ✅ ОПТИМИЗАЦИЯ: Фильтрация множественных hangup и отложенный сброс счетчиков
                should_send_event = True
                if amiEvent.Event in ('localcallEnd', 'incallEnd', 'outcallEnd'):
                    global _hangup_sent
                    # Для внешних звонков отправляем только первый hangup
                    if _hangup_sent:
                        should_send_event = False  # Пропускаем повторные hangup события
                    else:
                        _hangup_sent = True
                        # Оставляем флаги активными до завершения всех событий звонка

                if should_send_event:
                    if apiAlternative == 1:
                        apiAlternativeQueue.put(amiEvent)

                    if apiAlternative2 == 1:
                        apiAlternative2Queue.put(amiEvent)

            # for call in Call_List:
            #     if call.Uniqueid == message.Uniqueid:
            #         call.CallerIDNum = message.Value



        elif message.Variable == 'LisAMI_GetCustomerData':
            GetCustomerData_temp = message.Value.split(sep='_')
            status_temp = 'OK' if GetCustomerData_temp[2] == 'OK' else '<Response [404]>'
            DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(event = 'GetCustomerData', request = GetCustomerData_temp[0], status = status_temp, response = GetCustomerData_temp[2])))
            



# Смотрим событие NewCallerid c необходимыми полями
@manager.register_event('NewCallerid')
def NewCallerid(manager, message):
    global DbQueue

    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid

    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))
    
    # ✅ МОДЕРНИЗАЦИЯ: Отправляем NewCallerid в альтернативный API (с фильтрацией для внешних звонков)
    if apiAlternative == 1:
        # Проверяем, нужно ли фильтровать это событие для внешних звонков
        if not should_filter_new_callerid_event(message):
            apiAlternativeQueue.put(amiEvent)

    if apiAlternative2 == 1:
        # Применяем ту же фильтрацию для второго API
        if not should_filter_new_callerid_event(message):
            apiAlternative2Queue.put(amiEvent)



# Смотрим событие DialBegin c необходимыми полями
@manager.register_event('DialBegin')
def DialBegin(manager, message):
    global DbQueue, Channel_list

     # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid
    amiEvent.DestChannel = message.DestChannel
    amiEvent.DestChannelState = message.DestChannelState
    amiEvent.DestChannelStateDesc = message.DestChannelStateDesc
    amiEvent.DestCallerIDNum = message.DestCallerIDNum
    amiEvent.DestCallerIDName = message.DestCallerIDName
    amiEvent.DestConnectedLineNum = message.DestConnectedLineNum
    amiEvent.DestConnectedLineName = message.DestConnectedLineName
    amiEvent.DestAccountCode = message.DestAccountCode
    amiEvent.DestContext = message.DestContext
    amiEvent.DestExten = message.DestExten
    amiEvent.DestPriority = message.DestPriority
    amiEvent.DestUniqueid = message.DestUniqueid
    amiEvent.DialString = message.DialString
 



    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))

    # # добавляем данные в нужный канал
    # for ch in Channel_list:
    #     if ch.Channel == message.DestChannel:
    #         ch.CallUniqueid = message.Uniqueid

    # # поиск вызова в списке вызовов 
    # for c in Call_List:
    #     if c.Uniqueid == message.Uniqueid:            
    #         if c.DialSended == 0:
    #             # добавляем необходимые поля
    #             amiEvent.DialNum = c.DialNum
    #             amiEvent.CallType = c.CallType
    #             amiEvent.Trunk = c.Trunk
    #             amiEvent.CallerIDNum = c.CallerIDNum

    #             # Ставим событие Dial в очередь на отправку            
    #             q.put(amiEvent)

    #         c.DialSended = 1        # ставим отметку что dial отправлен
    pass
    

  
# Смотрим событие DialEnd c необходимыми полями
@manager.register_event('DialEnd')
def DialEnd(manager, message):
    global DbQueue, Call_List

    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid
    amiEvent.DestChannel = message.DestChannel
    amiEvent.DestChannelState = message.DestChannelState
    amiEvent.DestChannelStateDesc = message.DestChannelStateDesc
    amiEvent.DestCallerIDNum = message.DestCallerIDNum
    amiEvent.DestCallerIDName = message.DestCallerIDName
    amiEvent.DestConnectedLineNum = message.DestConnectedLineNum
    amiEvent.DestConnectedLineName = message.DestConnectedLineName
    amiEvent.DestAccountCode = message.DestAccountCode
    amiEvent.DestContext = message.DestContext
    amiEvent.DestExten = message.DestExten
    amiEvent.DestPriority = message.DestPriority
    amiEvent.DestUniqueid = message.DestUniqueid
    amiEvent.DialStatus = message.DialStatus

    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))

    # for call in Call_List:
    #     if call.Uniqueid == message.Uniqueid:
    #         call.DialStatus = message.DialStatus
    pass



# Смотрим событие BridgeCreate c необходимыми полями
@manager.register_event('BridgeCreate')
def BridgeCreate(manager, message):
    global DbQueue


    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege    
    amiEvent.BridgeUniqueid = message.BridgeUniqueid
    amiEvent.ChanBridgeTypenel = message.ChanBridgeTypenel
    amiEvent.BridgeTechnology = message.BridgeTechnology
    amiEvent.BridgeCreator = message.BridgeCreator
    amiEvent.BridgeName = message.BridgeName
    amiEvent.BridgeNumChannels = message.BridgeNumChannels
    
    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))
    
    # ✅ МОДЕРНИЗАЦИЯ: Отправляем BridgeCreate в альтернативный API (с фильтрацией для внешних звонков)
    if apiAlternative == 1:
        # Проверяем, нужно ли фильтровать это событие для внешних звонков
        if not should_filter_bridge_create_destroy_event(message):
            apiAlternativeQueue.put(amiEvent)

    if apiAlternative2 == 1:
        # Применяем ту же фильтрацию для второго API
        if not should_filter_bridge_create_destroy_event(message):
            apiAlternative2Queue.put(amiEvent)


# Смотрим событие BridgeEnter c необходимыми полями
@manager.register_event('BridgeEnter')
def BridgeEnter(manager, message):
    global q, DbQueue, apiAlternativeQueue

    call = None
    # поиск вызова в списке вызовов
    for c in Call_List:
        if c.Uniqueid == message.Uniqueid:
            call = c


    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    # Проверки
    phone = None
    if message.CallerIDNum in ('', '<unknown>'):
        phone = call.CallerIDNum
                
    else:
        phone = message.CallerIDNum

    CallerIDName = None
    if message.CallerIDName in ('Request failed with status code 400', '<unknown>'):
        CallerIDName = ''

    else:
        CallerIDName = message.CallerIDName



    amiEvent.Privilege = message.Privilege
    amiEvent.BridgeUniqueid = message.BridgeUniqueid
    amiEvent.ChanBridgeTypenel = message.ChanBridgeTypenel
    amiEvent.BridgeTechnology = message.BridgeTechnology
    amiEvent.BridgeCreator = message.BridgeCreator
    amiEvent.BridgeName = message.BridgeName
    amiEvent.BridgeNumChannels = message.BridgeNumChannels
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = phone
    amiEvent.CallerIDName = CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid


    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))
    q.put(amiEvent)

    # ✅ ОПТИМИЗАЦИЯ: Фильтрация bridge событий для внешних звонков
    if apiAlternative == 1:
        # Проверяем, нужно ли фильтровать это событие для внешних звонков
        if not should_filter_bridge_event(message) and not should_limit_bridge_events(message, 'bridge'):
            apiAlternativeQueue.put(amiEvent)

    if apiAlternative2 == 1:
        # Применяем ту же фильтрацию для второго API
        if not should_filter_bridge_event(message) and not should_limit_bridge_events(message, 'bridge'):
            apiAlternative2Queue.put(amiEvent)

    # for call in Call_List:
    #     if call.Uniqueid == message.Uniqueid:
    #         call.LastBridgeNum = message.ConnectedLineNum
    pass



# Смотрим событие BridgeLeave c необходимыми полями
@manager.register_event('BridgeLeave')
def BridgeLeave(manager, message):
    global DbQueue

     # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.BridgeUniqueid = message.BridgeUniqueid
    amiEvent.ChanBridgeTypenel = message.ChanBridgeTypenel
    amiEvent.BridgeTechnology = message.BridgeTechnology
    amiEvent.BridgeCreator = message.BridgeCreator
    amiEvent.BridgeName = message.BridgeName
    amiEvent.BridgeNumChannels = message.BridgeNumChannels
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid

    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))
    
    # ✅ МОДЕРНИЗАЦИЯ: Отправляем BridgeLeave в альтернативный API (с фильтрацией для внешних звонков)
    if apiAlternative == 1:
        # Проверяем, нужно ли фильтровать это событие для внешних звонков
        if not should_filter_bridge_event(message) and not should_limit_bridge_events(message, 'bridge_leave'):
            apiAlternativeQueue.put(amiEvent)

    if apiAlternative2 == 1:
        # Применяем ту же фильтрацию для второго API
        if not should_filter_bridge_event(message) and not should_limit_bridge_events(message, 'bridge_leave'):
            apiAlternative2Queue.put(amiEvent)



# Смотрим событие BridgeDestroy c необходимыми полями
@manager.register_event('BridgeDestroy')
def BridgeDestroy(manager, message):
    global DbQueue

     # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.BridgeUniqueid = message.BridgeUniqueid
    amiEvent.ChanBridgeTypenel = message.ChanBridgeTypenel
    amiEvent.BridgeTechnology = message.BridgeTechnology
    amiEvent.BridgeCreator = message.BridgeCreator
    amiEvent.BridgeName = message.BridgeName
    amiEvent.BridgeNumChannels = message.BridgeNumChannels

    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))
    
    # ✅ МОДЕРНИЗАЦИЯ: Отправляем BridgeDestroy в альтернативный API (с фильтрацией для внешних звонков)
    if apiAlternative == 1:
        # Проверяем, нужно ли фильтровать это событие для внешних звонков
        if not should_filter_bridge_create_destroy_event(message):
            apiAlternativeQueue.put(amiEvent)

    if apiAlternative2 == 1:
        # Применяем ту же фильтрацию для второго API
        if not should_filter_bridge_create_destroy_event(message):
            apiAlternative2Queue.put(amiEvent)



# Смотрим событие HangupRequest c необходимыми полями
@manager.register_event('HangupRequest')
def HangupRequest(manager, message):
    global DbQueue

    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid




    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))



# Смотрим событие SoftHangupRequest c необходимыми полями
@manager.register_event('SoftHangupRequest')
def SoftHangupRequest(manager, message):
    global DbQueue, Call_List


    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid
    amiEvent.Cause = message.Cause
    

    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))

    # # ставии пометку для вызова
    # for call in Call_List:
    #     if call.Uniqueid == message.Uniqueid:
    #         call.SoftHangupRequest = 1





# Смотрим событие Hangup c необходимыми полями
@manager.register_event('Hangup')
def Hangup(manager, message):
    global DbQueue, q, Channel_list, Call_List, apiAlternativeQueue

    # создаем объект
    amiEvent = AMIEvent(Event = message.Event)

    amiEvent.Privilege = message.Privilege
    amiEvent.Channel = message.Channel
    amiEvent.ChannelState = message.ChannelState
    amiEvent.ChannelStateDesc = message.ChannelStateDesc
    amiEvent.CallerIDNum = message.CallerIDNum
    amiEvent.CallerIDName = message.CallerIDName
    amiEvent.ConnectedLineNum = message.ConnectedLineNum
    amiEvent.ConnectedLineName = message.ConnectedLineName
    amiEvent.AccountCode = message.AccountCode
    amiEvent.Context = message.Context
    amiEvent.Exten = message.Exten
    amiEvent.Priority = message.Priority
    amiEvent.Uniqueid = message.Uniqueid
    amiEvent.Cause = message.Cause
    

    # добавление в очередь
    DbQueue.put(DbItem(DB_Type.AMI_Event, amiEvent))

    # # удаляем канал из списка   
    # ch_i = None
    # for index, ch in enumerate(Channel_list):
    #     if ch.Uniqueid == message.Uniqueid:
    #         ch_i = index

    # print(ch_i)
    # if ch_i != None:
    #     del Channel_list[ch_i]


    # call = None
    # # поиск вызова в списке вызовов
    # for c in Call_List:
    #     if c.Uniqueid == message.Uniqueid:
    #         call = c

    # if call != None:        # Если найден вызов в списке вызовов
    #     # дополняем данными из call
    #     amiEvent.CallType = call.CallType
    #     amiEvent.PhoneTo = call.PhoneTo
    #     amiEvent.DialStatus = call.DialStatus
    #     amiEvent.LastBridgeNum = call.LastBridgeNum
    #     amiEvent.DateTimeStart = call.DateTimeStart
    #     amiEvent.Trunk = call.Trunk
    #     amiEvent.CallerIDNum = c.CallerIDNum

    #     # ставим в очередь
    #     q.put(amiEvent)

    #     # удаляем из списка вызовов
    #     if call in Call_List:
    #         Call_List.remove(call)

    #     # # для отладки
    #     # #res = json.dumps(Channel_list)
    #     # print('Hangup')
    #     # print('Channel_list---------------------------------')
    #     # print(*Channel_list, sep='\n')
    #     # print('Call_List------------------------------------')
    #     # print(*Call_List, sep='\n')
    #     # print('---------------------------------------------')

    #     pass
    pass












# для потока setTime_thrad (установки времени в файле /etc/rc.local)
def setTime():

    dt = datetime.now().strftime("%d %b %Y %H:%M:%S")

    rc_file = open("/etc/rc.local", "w")
    rc_file.write("#!/bin/sh -e\n")
    rc_file.write("#\n")
    rc_file.write("# rc.local\n")
    rc_file.write("# This script is executed at the end of each multiuser runlevel.\n")
    rc_file.write('# Make sure that the script will "exit 0" on success or any other\n')
    rc_file.write("# value on error.\n")
    rc_file.write("#\n")
    rc_file.write("# In order to enable or disable this script just change the execution\n")
    rc_file.write("# bits.\n")
    rc_file.write("#\n")
    rc_file.write("# By default this script does nothing.\n")
    rc_file.write("#\n")
    rc_file.write("#Set last know Date and Time\n")
    rc_file.write('date -s "' + dt + '"\n')
    rc_file.write("/etc/rc.firewall\n")
    rc_file.write("/etc/settime.sh\n")
    rc_file.write("exit 0\n")

    #print('SetTime: '+dt)

    rc_file.close()
    
    


# отправка POST запроса в CRM
def crm_post(data: AMIEvent):
    global UnitID, pingState, Call_List

    event_api_type = ''
    Body = None

    headers = {'Content-type': 'application/json',  # Определение типа данных
           #'Accept': 'text/plain',
                                                #'Content-Encoding': 'utf-8',
                                                #'Content-Length': len(UnitID),
           'Token': UnitID}



    if data.Event == 'localcallStart':  
        event_api_type = 'dial'

        # #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${ARG1}"],"CallType":"2","Trunk":"","ExtTrunk":"","ExtPhone":""})
        #         #same => n,Set(LisAMI_EventData="localcall_start"&${CALLERID(num)}"&${ARG1})

        #         amiEvent =  AMIEvent(Event = 'localcallStart')

        #         amiEvent.Uniqueid = message.Uniqueid
        #         amiEvent.Phone = temp_data[1]
        #         amiEvent.Extensions = temp_data[2]
        

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 2,
        'Trunk' : '',
        'ExtTrunk': '',
        'ExtPhone': ''
        }


    elif data.Event == 'localcallEnd': 
        event_api_type = 'hangup'

                # #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${DIALEDPEERNUMBER}"],"CallType":"2","DateReceived":"${CDR(start)}",
                # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}"})
                # #exten => s,1,Set(LisAMI_EventData="localcall_end"&${CALLERID(num)}"&${DIALEDPEERNUMBER}&${CDR(start)&${CallStartDT}&${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)})
                # amiEvent =  AMIEvent(Event = 'localcallEnd')

                # amiEvent.Uniqueid = message.Uniqueid
                # amiEvent.Phone = temp_data[1]
                # amiEvent.Extensions = temp_data[2]
                # amiEvent.DateReceived = temp_data[3]
                # amiEvent.StartTime = temp_data[4]
                # amiEvent.EndTime = temp_data[5]
                # amiEvent.CallStatus = temp_data[6]

        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 2,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus       
        }


    elif data.Event == 'incallStart': 
        event_api_type = 'start'

            # elif temp_data[0] == 'incallStart':
            #     #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","CallType":"0","Trunk":"${ARG1}"})
            #     # same => n,Set(LisAMI_EventData="incall_start"&${CALLERID(num)}"&${ARG1})

            #     amiEvent =  AMIEvent(Event = 'incallStart')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Trunk = temp_data[2]

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'CallType' : 0,
        'Trunk' : data.Trunk
        }


    elif data.Event == 'incallDial': 
        event_api_type = 'dial'

                
            # elif temp_data[0] == 'incallDial':
            #     # ;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${Exten}"],"CallType":"0","Trunk":"${ARG1}",
            # "ExtTrunk":"","ExtPhone":"${CALLERID(num)}"})
            #     #same => n,Set(LisAMI_EventData="incall_dial"&${CALLERID(num)}"&${ARG2}&${ARG1})

            #     amiEvent =  AMIEvent(Event = 'incallDial')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Trunk = temp_data[2]


        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 0,
        'Trunk' : data.Trunk,
        'ExtTrunk': '',
        'ExtPhone': data.Phone
        }


    elif data.Event == 'incallEnd': 
        event_api_type = 'hangup'
                
            # elif temp_data[0] == 'incallEnd':
            #     #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${DIALEDPEERNUMBER}"],"CallType":"2","DateReceived":"${CDR(start)}",
            # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})
            #     #exten => s,1,Set(LisAMI_EventData=incallEnd_${CALLERID(num)}_${DIALEDPEERNUMBER}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${ARG1}_${DIALSTATUS})

            #     amiEvent =  AMIEvent(Event = 'incallEnd')

                # amiEvent.Uniqueid = message.Uniqueid
                # amiEvent.Phone = temp_data[1]
                # amiEvent.Extensions = temp_data[2]
                # amiEvent.DateReceived = temp_data[3]
                # amiEvent.StartTime = temp_data[4]
                # amiEvent.EndTime = temp_data[5]
                # amiEvent.Trunk = temp_data[6]
                # amiEvent.CallStatus = temp_data[7]


        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 0,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus,
        'Trunk' : data.Trunk    
        }


    elif data.Event == 'outcallDial': 
        event_api_type = 'dial'

            # elif temp_data[0] == 'outcallDial':
            #     #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${ARG2}","Extensions":["${CALLERID(num)}"],"CallType":"1","Trunk":"${ARG1}","ExtTrunk":"","ExtPhone":""})
            #     #same => n,Set(LisAMI_EventData="outcall_dial"&${ARG2}&${CALLERID(num)}"&${ARG1})

                
            #     amiEvent =  AMIEvent(Event = 'outcallDial')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Extensions = temp_data[2]
            #     amiEvent.Trunk = temp_data[3]

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 1,        
        'Trunk' : data.Trunk,
        'ExtTrunk': '',
        'ExtPhone' : ''
        }


    elif data.Event == 'outcallEnd': 
        event_api_type = 'hangup'

            # #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${PhoneTo}","Extensions":["${CALLERID(num)}"],"CallType":"1","DateReceived":"${CDR(start)}",
            # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})
            #     # exten => s,1,Set(LisAMI_EventData=outcallEnd_${PhoneTo}_${CALLERID(num)}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${ARG1}_${DIALSTATUS})

                
            #     amiEvent =  AMIEvent(Event = 'outcallEnd')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Extensions = temp_data[2]
            #     amiEvent.DateReceived = temp_data[3]
            #     amiEvent.StartTime = temp_data[4]
            #     amiEvent.EndTime = temp_data[5]
            #     amiEvent.Trunk = temp_data[6]
            #     amiEvent.CallStatus = temp_data[7]
                

        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 1,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus,
        'Trunk' : data.Trunk    
        }



    # [macro-incall_start] контекст события уже отфильтрован по Context =
    # from-out-office
    #  https://crm.vochi.by/api/callevent/start
    # if data.Event == 'Newchannel':  
    #     event_api_type = 'start'

    #     #{"Token":"375291919585","UniqueId":"1718864207.6","Phone":"+375296681309","CallType":"0","Trunk":"0001363"}

    #     # подготовка Trunk
    #     #print('Newchannel')
    #     #print(repr(data.Channel))

    #     trunk_temp1 = data.Channel.split('/')
    #     #print('trunk_temp1: '+repr(trunk_temp1))

    #     trunk_temp2 = trunk_temp1[1].split('-')

    #     #print('trunk_temp2: '+repr(trunk_temp2))

    #     trunk = trunk_temp2[0]

    #     #print('trunk: '+trunk)



    #     Body = {
    #     'Token' : UnitID,
    #     'UniqueId' : data.Uniqueid,
    #     'Phone' : data.CallerIDNum,
    #     'CallType' : 0,
    #     'Trunk' : trunk
    #     }


    elif data.Event == 'BridgeEnter':
        event_api_type = 'bridge'

        # подготовка данных

        # {"Token":"375291448457
        # ","UniqueId":"1718883568.41","Channel":"SIP\/0001368-00000015","Exten":"3","CallerIDNum":"+375293193330","CallerIDName":"+375293193330","ConnectedLineNum":"152","ConnectedLineName":"<unknown>"}

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Channel' : data.Channel,
        'Exten' : data.Exten,
        'CallerIDNum' : data.CallerIDNum,
        'CallerIDName' : data.CallerIDName,
        'ConnectedLineNum' : data.ConnectedLineNum,
        'ConnectedLineName' : data.ConnectedLineName
        }


    # elif data.Event == 'DialBegin':
    #     event_api_type = 'dial'

    #     # подготовка данных
    #     phone = data.CallerIDNum
    #     extensions = data.DialNum


    #     # {"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${Exten}"],"CallType":"0","Trunk":"${ARG1}","ExtTrunk":"","ExtPhone":"${CALLERID(num)}"})
            

    #     # in - "CallType":"0"
    #     # local - "CallType":"2"
    #     # out - "CallType":"1"
    #     CallType = '0'
    #     if data.CallType == 'local':
    #         CallType = '2'

    #     elif data.CallType == 'out':
    #         CallType = '1'
    #         phone = data.DialNum[0]
    #         extensions = [data.CallerIDNum]


    #     Body = {
    #     'Token' : UnitID,
    #     'UniqueId' : data.Uniqueid,
    #     'Phone' : phone,
    #     'Extensions' : extensions,
    #     'CallType' : CallType,
    #     'Trunk' : data.Trunk,
    #     'ExtTrunk' : '',
    #     'ExtPhone' : phone
    #     }


    # elif data.Event == 'Hangup':
    #     event_api_type = 'hangup'

    #     # local - ${CALLERID(num)
    #     # incall - ${CALLERID(num)}
    #     # outcall - ${PhoneTo}
    #     Phone = data.CallerIDNum
        

    #     # in - "CallType":"0"
    #     # local - "CallType":"2"
    #     # out - "CallType":"1"
    #     CallType = '0'
    #     if data.CallType == 'local':
    #         CallType = '2'
    #     elif data.CallType == 'out':
    #         CallType = '1'
    #         Phone = data.PhoneTo

    #     CallStatus = ''
    #     if data.DialStatus == '':
    #         CallStatus = '1'
    #     elif data.DialStatus == 'CANCEL':
    #         CallStatus = '0'
    #     elif data.DialStatus == 'OTHER':
    #         CallStatus = '1'
    #     elif data.DialStatus == 'ANSWER':
    #         CallStatus = '2'
    #     elif data.DialStatus == 'TALK':
    #         CallStatus = '5'
    #     elif data.DialStatus == 'NOANSWER':
    #         CallStatus = '0'
    #     elif data.DialStatus == 'BUSY':
    #         CallStatus = '0'
    #     elif data.DialStatus == 'CONGESTION':
    #         CallStatus = '0'
    #     elif data.DialStatus == 'CHANUNAVAIL':
    #         CallStatus = '0'

        
    #     # подготовка данных

    #     # {"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${PhoneTo}","Extensions":["${CALLERID(num)}"],"CallType":"1","DateReceived":"${CDR(start)}","StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})

    #     extensions = [data.LastBridgeNum]
    #     if data.LastBridgeNum == '<unknown>':
    #         extensions = ''
        

    #     Body = {
    #     'Token' : UnitID,
    #     'UniqueId' : data.Uniqueid,
    #     'Phone' : Phone,
    #     'Extensions' : extensions,
    #     'CallType' : CallType,
    #     'DateReceived' : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    #     'StartTime' : data.DateTimeStart,
    #     'EndTime' : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    #     'CallStatus' : CallStatus,
    #     'Trunk' : data.Trunk
    #     }



    if Body:
        
        # ✅ Добавляем маркер внешней инициации для внешних звонков
        Body = add_external_marker_to_body(Body, data)

        # сериализуем данные для отправки
        d1 = json.dumps(Body)

        print(event_api_type + ':\n' + d1+'\n')

        resTime = 0
    
        # отправляем
        try:
            if pingState == 0:
                answer = ''

                start = time.time() ## точка отсчета времени

                answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=20, verify=False)

                resTime = time.time() - start ## длительность запроса

                print(repr(answer) + ' - ' + repr(resTime) + ' s')

            else:
                answer = Response()
            


        except requests.Timeout:
            print('Timeout')
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '', responseTime = resTime)))

            # # попытка отправить еще раз #1
            # try:
            #     if pingState == 0:
            #         answer = ''

            #         start = time.time() ## точка отсчета времени

            #         answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=8)

            #         resTime = time.time() - start ## длительность запроса

            #         print(repr(answer) + ' - ' + repr(resTime) + ' s')

            #     else:
            #         answer = Response()
            
            # except requests.Timeout:
            #     print('Timeout')
            #     # добавление в очередь на запись в БД
            #     DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '', responseTime = resTime)))

                # # попытка отправить еще раз #2
                # try:
                #     if pingState == 0:
                #         answer = ''
                #         answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=1)

                #     else:
                #         answer = Response()
            
                # except requests.Timeout:
                #     print('Timeout')
                #     # добавление в очередь на запись в БД
                #     DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '')))

                #     # попытка отправить еще раз #3
                #     try:
                #         if pingState == 0:
                #             answer = ''
                #             answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=1)

                #         else:
                #             answer = Response()
            
                #     except requests.Timeout:
                #         print('Timeout')
                #         # добавление в очередь на запись в БД
                #         DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '')))

                #         # попытка отправить еще раз #4
                #         try:
                #             if pingState == 0:
                #                 answer = ''
                #                 answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=1)

                #             else:
                #                 answer = Response()
            
                #         except requests.Timeout:
                #             print('Timeout')
                #             # добавление в очередь на запись в БД
                #             DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '')))

        except requests.ConnectionError:
            print('ConnectionError')
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'ConnectionError', response = '', responseTime = resTime)))

        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

            stack_trace_string = ''
            for line in stack_trace:
                stack_trace_string += line + '\n'

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', "Exception message : %s" %ex_value + '   Stack trace: ' + stack_trace_string)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###  error post : %s" % stack_trace)

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Error '+repr(answer), response = answer.text, responseTime = resTime)))

        if pingState == 0:
            print('answer:' + answer.text)

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = repr(answer), response = answer.text, responseTime = resTime)))

            pass

        else:
            print('answer: None connection')
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = '<Response [404]>', response = 'None connection (Not ping)', responseTime = resTime)))

            pass

# отправка POST запроса в CRM
def Alternative_crm_post(data: AMIEvent):
    global UnitID, pingState, Call_List

    # переменная для отслеживания ошибки отправки 0- нет ошибки 1- есть ошибка
    errorSend = 0
    errorText = ''

    event_api_type = ''
    Body = None

    headers = {'Content-type': 'application/json',  # Определение типа данных
           #'Accept': 'text/plain',
                                                #'Content-Encoding': 'utf-8',
                                                #'Content-Length': len(UnitID),
           'Token': UnitID}



    if data.Event == 'localcallStart':  
        event_api_type = 'dial'
        
        # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","Trunk":"","ExtTrunk":"","ExtPhone":""}


        # #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${ARG1}"],"CallType":"2","Trunk":"","ExtTrunk":"","ExtPhone":""})
        #         #same => n,Set(LisAMI_EventData="localcall_start"&${CALLERID(num)}"&${ARG1})

        #         amiEvent =  AMIEvent(Event = 'localcallStart')

        #         amiEvent.Uniqueid = message.Uniqueid
        #         amiEvent.Phone = temp_data[1]
        #         amiEvent.Extensions = temp_data[2]
        

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 2,
        'Trunk' : '',
        'ExtTrunk': '',
        'ExtPhone': ''
        }


    elif data.Event == 'localcallEnd': 
        event_api_type = 'hangup'

                # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","DateReceived":"","StartTime":"","EndTime":"","CallStatus":""}

                # #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${DIALEDPEERNUMBER}"],"CallType":"2","DateReceived":"${CDR(start)}",
                # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}"})
                # #exten => s,1,Set(LisAMI_EventData="localcall_end"&${CALLERID(num)}"&${DIALEDPEERNUMBER}&${CDR(start)&${CallStartDT}&${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)})
                # amiEvent =  AMIEvent(Event = 'localcallEnd')

                # amiEvent.Uniqueid = message.Uniqueid
                # amiEvent.Phone = temp_data[1]
                # amiEvent.Extensions = temp_data[2]
                # amiEvent.DateReceived = temp_data[3]
                # amiEvent.StartTime = temp_data[4]
                # amiEvent.EndTime = temp_data[5]
                # amiEvent.CallStatus = temp_data[6]

        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 2,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus       
        }


    elif data.Event == 'incallStart': 
        event_api_type = 'start'

            # {"Token":"","UniqueId":"","Phone":"","CallType":"","Trunk":""}
            # elif temp_data[0] == 'incallStart':
            #     #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","CallType":"0","Trunk":"${ARG1}"})
            #     # same => n,Set(LisAMI_EventData="incall_start"&${CALLERID(num)}"&${ARG1})

            #     amiEvent =  AMIEvent(Event = 'incallStart')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Trunk = temp_data[2]

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'CallType' : 0,
        'Trunk' : data.Trunk
        }


    elif data.Event == 'incallDial': 
        event_api_type = 'dial'

                
            # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","Trunk":"","ExtTrunk":"","ExtPhone":""}

            # elif temp_data[0] == 'incallDial':
            #     # ;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${Exten}"],"CallType":"0","Trunk":"${ARG1}",
            # "ExtTrunk":"","ExtPhone":"${CALLERID(num)}"})
            #     #same => n,Set(LisAMI_EventData="incall_dial"&${CALLERID(num)}"&${ARG2}&${ARG1})

            #     amiEvent =  AMIEvent(Event = 'incallDial')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Trunk = temp_data[2]


        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 0,
        'Trunk' : data.Trunk,
        'ExtTrunk': '',
        'ExtPhone': data.Phone
        }


    elif data.Event == 'incallEnd': 
        event_api_type = 'hangup'
                
            # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","DateReceived":"","StartTime":"","EndTime":"","CallStatus":"","Trunk":""}

            # elif temp_data[0] == 'incallEnd':
            #     #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${DIALEDPEERNUMBER}"],"CallType":"2","DateReceived":"${CDR(start)}",
            # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})
            #     #exten => s,1,Set(LisAMI_EventData=incallEnd_${CALLERID(num)}_${DIALEDPEERNUMBER}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${ARG1}_${DIALSTATUS})

            #     amiEvent =  AMIEvent(Event = 'incallEnd')

                # amiEvent.Uniqueid = message.Uniqueid
                # amiEvent.Phone = temp_data[1]
                # amiEvent.Extensions = temp_data[2]
                # amiEvent.DateReceived = temp_data[3]
                # amiEvent.StartTime = temp_data[4]
                # amiEvent.EndTime = temp_data[5]
                # amiEvent.Trunk = temp_data[6]
                # amiEvent.CallStatus = temp_data[7]


        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 0,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus,
        'Trunk' : data.Trunk    
        }


    elif data.Event == 'outcallDial': 
        event_api_type = 'dial'

            # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","Trunk":"","ExtTrunk":"","ExtPhone":""}

            # elif temp_data[0] == 'outcallDial':
            #     #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${ARG2}","Extensions":["${CALLERID(num)}"],"CallType":"1","Trunk":"${ARG1}","ExtTrunk":"","ExtPhone":""})
            #     #same => n,Set(LisAMI_EventData="outcall_dial"&${ARG2}&${CALLERID(num)}"&${ARG1})

                
            #     amiEvent =  AMIEvent(Event = 'outcallDial')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Extensions = temp_data[2]
            #     amiEvent.Trunk = temp_data[3]

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 1,        
        'Trunk' : data.Trunk,
        'ExtTrunk': '',
        'ExtPhone' : ''
        }


    elif data.Event == 'outcallEnd': 
        event_api_type = 'hangup'

            # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","DateReceived":"","StartTime":"","EndTime":"","CallStatus":"","Trunk":""}

            # #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${PhoneTo}","Extensions":["${CALLERID(num)}"],"CallType":"1","DateReceived":"${CDR(start)}",
            # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})
            #     # exten => s,1,Set(LisAMI_EventData=outcallEnd_${PhoneTo}_${CALLERID(num)}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${ARG1}_${DIALSTATUS})

                
            #     amiEvent =  AMIEvent(Event = 'outcallEnd')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Extensions = temp_data[2]
            #     amiEvent.DateReceived = temp_data[3]
            #     amiEvent.StartTime = temp_data[4]
            #     amiEvent.EndTime = temp_data[5]
            #     amiEvent.Trunk = temp_data[6]
            #     amiEvent.CallStatus = temp_data[7]
                

        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 1,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus,
        'Trunk' : data.Trunk    
        }



    # [macro-incall_start] контекст события уже отфильтрован по Context =
    # from-out-office
    #  https://crm.vochi.by/api/callevent/start
    # if data.Event == 'Newchannel':  
    #     event_api_type = 'start'

    #     #{"Token":"375291919585","UniqueId":"1718864207.6","Phone":"+375296681309","CallType":"0","Trunk":"0001363"}

    #     # подготовка Trunk
    #     #print('Newchannel')
    #     #print(repr(data.Channel))

    #     trunk_temp1 = data.Channel.split('/')
    #     #print('trunk_temp1: '+repr(trunk_temp1))

    #     trunk_temp2 = trunk_temp1[1].split('-')

    #     #print('trunk_temp2: '+repr(trunk_temp2))

    #     trunk = trunk_temp2[0]

    #     #print('trunk: '+trunk)



    #     Body = {
    #     'Token' : UnitID,
    #     'UniqueId' : data.Uniqueid,
    #     'Phone' : data.CallerIDNum,
    #     'CallType' : 0,
    #     'Trunk' : trunk
    #     }


    elif data.Event == 'BridgeEnter':
        event_api_type = 'bridge'

        # подготовка данных

        # {"Token":"","UniqueId":"","Channel":"","Exten":"","CallerIDNum":"","CallerIDName":"","ConnectedLineNum":"","ConnectedLineName":""}
        
        # {"Token":"375291448457","UniqueId":"1718883568.41","Channel":"SIP\/0001368-00000015","Exten":"3","CallerIDNum":"+375293193330","CallerIDName":"+375293193330","ConnectedLineNum":"152","ConnectedLineName":"<unknown>"}

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'BridgeUniqueid' : data.BridgeUniqueid,
        'Channel' : data.Channel,
        'Exten' : data.Exten,
        'CallerIDNum' : data.CallerIDNum,
        'CallerIDName' : data.CallerIDName,
        'ConnectedLineNum' : data.ConnectedLineNum,
        'ConnectedLineName' : data.ConnectedLineName
        }


    # elif data.Event == 'DialBegin':
    #     event_api_type = 'dial'

    #     # подготовка данных
    #     phone = data.CallerIDNum
    #     extensions = data.DialNum


    #     # {"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${Exten}"],"CallType":"0","Trunk":"${ARG1}","ExtTrunk":"","ExtPhone":"${CALLERID(num)}"})
            

    #     # in - "CallType":"0"
    #     # local - "CallType":"2"
    #     # out - "CallType":"1"
    #     CallType = '0'
    #     if data.CallType == 'local':
    #         CallType = '2'

    #     elif data.CallType == 'out':
    #         CallType = '1'
    #         phone = data.DialNum[0]
    #         extensions = [data.CallerIDNum]


    #     Body = {
    #     'Token' : UnitID,
    #     'UniqueId' : data.Uniqueid,
    #     'Phone' : phone,
    #     'Extensions' : extensions,
    #     'CallType' : CallType,
    #     'Trunk' : data.Trunk,
    #     'ExtTrunk' : '',
    #     'ExtPhone' : phone
    #     }


    # elif data.Event == 'Hangup':
    #     event_api_type = 'hangup'

    #     # local - ${CALLERID(num)
    #     # incall - ${CALLERID(num)}
    #     # outcall - ${PhoneTo}
    #     Phone = data.CallerIDNum
        

    #     # in - "CallType":"0"
    #     # local - "CallType":"2"
    #     # out - "CallType":"1"
    #     CallType = '0'
    #     if data.CallType == 'local':
    #         CallType = '2'
    #     elif data.CallType == 'out':
    #         CallType = '1'
    #         Phone = data.PhoneTo

    #     CallStatus = ''
    #     if data.DialStatus == '':
    #         CallStatus = '1'
    #     elif data.DialStatus == 'CANCEL':
    #         CallStatus = '0'
    #     elif data.DialStatus == 'OTHER':
    #         CallStatus = '1'
    #     elif data.DialStatus == 'ANSWER':
    #         CallStatus = '2'
    #     elif data.DialStatus == 'TALK':
    #         CallStatus = '5'
    #     elif data.DialStatus == 'NOANSWER':
    #         CallStatus = '0'
    #     elif data.DialStatus == 'BUSY':
    #         CallStatus = '0'
    #     elif data.DialStatus == 'CONGESTION':
    #         CallStatus = '0'
    #     elif data.DialStatus == 'CHANUNAVAIL':
    #         CallStatus = '0'

        
    #     # подготовка данных

    #     # {"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${PhoneTo}","Extensions":["${CALLERID(num)}"],"CallType":"1","DateReceived":"${CDR(start)}","StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})

    #     extensions = [data.LastBridgeNum]
    #     if data.LastBridgeNum == '<unknown>':
    #         extensions = ''
        

    #     Body = {
    #     'Token' : UnitID,
    #     'UniqueId' : data.Uniqueid,
    #     'Phone' : Phone,
    #     'Extensions' : extensions,
    #     'CallType' : CallType,
    #     'DateReceived' : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    #     'StartTime' : data.DateTimeStart,
    #     'EndTime' : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    #     'CallStatus' : CallStatus,
    #     'Trunk' : data.Trunk
    #     }



    # ✅ МОДЕРНИЗАЦИЯ: Добавлены новые события Bridge и NewCallerid
    elif data.Event == 'BridgeCreate':
        event_api_type = 'bridge_create'
        
        Body = {
        'Token' : UnitID,
        'UniqueId' : '',  # У BridgeCreate нет UniqueId
        'BridgeUniqueid' : data.BridgeUniqueid,
        'BridgeType' : data.BridgeType if hasattr(data, 'BridgeType') else '',
        'BridgeTechnology' : data.BridgeTechnology,
        'BridgeCreator' : data.BridgeCreator,
        'BridgeName' : data.BridgeName,
        'BridgeNumChannels' : data.BridgeNumChannels
        }

    elif data.Event == 'BridgeLeave':
        event_api_type = 'bridge_leave'
        
        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'BridgeUniqueid' : data.BridgeUniqueid,
        'Channel' : data.Channel,
        'CallerIDNum' : data.CallerIDNum,
        'CallerIDName' : data.CallerIDName,
        'ConnectedLineNum' : data.ConnectedLineNum,
        'ConnectedLineName' : data.ConnectedLineName,
        'BridgeNumChannels' : data.BridgeNumChannels
        }

    elif data.Event == 'BridgeDestroy':
        event_api_type = 'bridge_destroy'
        
        Body = {
        'Token' : UnitID,
        'UniqueId' : '',  # У BridgeDestroy нет UniqueId
        'BridgeUniqueid' : data.BridgeUniqueid,
        'BridgeType' : data.BridgeType if hasattr(data, 'BridgeType') else '',
        'BridgeTechnology' : data.BridgeTechnology,
        'BridgeCreator' : data.BridgeCreator,
        'BridgeName' : data.BridgeName,
        'BridgeNumChannels' : data.BridgeNumChannels
        }

    elif data.Event == 'NewCallerid':
        event_api_type = 'new_callerid'
        
        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Channel' : data.Channel,
        'CallerIDNum' : data.CallerIDNum,
        'CallerIDName' : data.CallerIDName,
        'ConnectedLineNum' : data.ConnectedLineNum,
        'ConnectedLineName' : data.ConnectedLineName,
        'Context' : data.Context,
        'Exten' : data.Exten
        }

    if Body:
        
        # ✅ Добавляем маркер внешней инициации для внешних звонков
        Body = add_external_marker_to_body(Body, data)

        # сериализуем данные для отправки
        d1 = json.dumps(Body)

        print(event_api_type + ':\n' + d1+'\n')

        resTime = 0

        #print(apiAlternativeURL + event_api_type)
        #print(d1)

    
        # отправляем
        try:
            if pingState == 0:
                answer = ''

                start = time.time() ## точка отсчета времени

                answer = requests.post(apiAlternativeURL + event_api_type, data=d1, headers=headers, timeout=3, verify=False)
                #answer = requests.request("POST", apiAlternativeURL + event_api_type, data=d1, headers=headers, timeout=3)

                resTime = time.time() - start ## длительность запроса

                print(repr(answer) + ' - ' + repr(resTime) + ' s')

            else:
                answer = Response()
            


        except requests.Timeout:
            errorSend = 1
            errorText = 'Timeout'
            print('Timeout')
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.AlternativeAPI_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '', responseTime = resTime)))

            # # попытка отправить еще раз #1
            # try:
            #     if pingState == 0:
            #         answer = ''

            #         start = time.time() ## точка отсчета времени

            #         answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=8)

            #         resTime = time.time() - start ## длительность запроса

            #         print(repr(answer) + ' - ' + repr(resTime) + ' s')

            #     else:
            #         answer = Response()
            
            # except requests.Timeout:
            #     print('Timeout')
            #     # добавление в очередь на запись в БД
            #     DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '', responseTime = resTime)))

                # # попытка отправить еще раз #2
                # try:
                #     if pingState == 0:
                #         answer = ''
                #         answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=1)

                #     else:
                #         answer = Response()
            
                # except requests.Timeout:
                #     print('Timeout')
                #     # добавление в очередь на запись в БД
                #     DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '')))

                #     # попытка отправить еще раз #3
                #     try:
                #         if pingState == 0:
                #             answer = ''
                #             answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=1)

                #         else:
                #             answer = Response()
            
                #     except requests.Timeout:
                #         print('Timeout')
                #         # добавление в очередь на запись в БД
                #         DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '')))

                #         # попытка отправить еще раз #4
                #         try:
                #             if pingState == 0:
                #                 answer = ''
                #                 answer = requests.post(url + event_api_type, data=d1, headers=headers, timeout=1)

                #             else:
                #                 answer = Response()
            
                #         except requests.Timeout:
                #             print('Timeout')
                #             # добавление в очередь на запись в БД
                #             DbQueue.put(DbItem(DB_Type.API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '')))

        except requests.ConnectionError as e:
            errorSend = 1
            errorText = 'ConnectionError' + repr(e)
            print(errorText)
            
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.AlternativeAPI_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = errorText, response = '', responseTime = resTime)))

        except BaseException as ex:

            errorSend = 1
            errorText = repr(ex)

            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

            stack_trace_string = ''
            for line in stack_trace:
                stack_trace_string += line + '\n'

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', "Exception message : %s" %ex_value + '   Stack trace: ' + stack_trace_string)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###  error post : %s" % stack_trace)

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.AlternativeAPI_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Error '+repr(answer), response = answer.text, responseTime = resTime)))

        if pingState == 0:

            if answer:
                
                try:
                    ANSWERstring = answer.text.encode('ascii', 'ignore').decode('ascii')
                    print('answer:' + ANSWERstring)
                    # добавление в очередь на запись в БД
                    DbQueue.put(DbItem(DB_Type.AlternativeAPI_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = repr(answer), response = ANSWERstring, responseTime = resTime)))

                except:
                    print('answer: answer convert error')
                    # добавление в очередь на запись в БД
                    DbQueue.put(DbItem(DB_Type.AlternativeAPI_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = repr(answer), response = ANSWERstring, responseTime = resTime)))

                    

            else:
                # добавление в очередь на запись в БД
                DbQueue.put(DbItem(DB_Type.AlternativeAPI_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = repr(answer), response = '', responseTime = resTime)))


            pass

        else:
            print('answer: None connection')
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.AlternativeAPI_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = '<Response [404]>', response = 'None connection (Not ping)', responseTime = resTime)))

            pass





# отправка POST запроса во вторую альтернативную CRM
def Alternative2_crm_post(data: AMIEvent):
    global UnitID, pingState, Call_List

    # переменная для отслеживания ошибки отправки 0- нет ошибки 1- есть ошибка
    errorSend = 0
    errorText = ''

    event_api_type = ''
    Body = None

    headers = {'Content-type': 'application/json',  # Определение типа данных
           #'Accept': 'text/plain',
                                                #'Content-Encoding': 'utf-8',
                                                #'Content-Length': len(UnitID),
           'Token': UnitID}



    if data.Event == 'localcallStart':  
        event_api_type = 'dial'
        
        # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","Trunk":"","ExtTrunk":"","ExtPhone":""}


        # #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${ARG1}"],"CallType":"2","Trunk":"","ExtTrunk":"","ExtPhone":""})
        #         #same => n,Set(LisAMI_EventData="localcall_start"&${CALLERID(num)}"&${ARG1})

        #         amiEvent =  AMIEvent(Event = 'localcallStart')

        #         amiEvent.Uniqueid = message.Uniqueid
        #         amiEvent.Phone = temp_data[1]
        #         amiEvent.Extensions = temp_data[2]
        

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 2,
        'Trunk' : '',
        'ExtTrunk': '',
        'ExtPhone': ''
        }


    elif data.Event == 'localcallEnd': 
        event_api_type = 'hangup'

                # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","DateReceived":"","StartTime":"","EndTime":"","CallStatus":""}

                # #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${DIALEDPEERNUMBER}"],"CallType":"2","DateReceived":"${CDR(start)}",
                # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}"})
                # #exten => s,1,Set(LisAMI_EventData="localcall_end"&${CALLERID(num)}"&${DIALEDPEERNUMBER}&${CDR(start)&${CallStartDT}&${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)})
                # amiEvent =  AMIEvent(Event = 'localcallEnd')

                # amiEvent.Uniqueid = message.Uniqueid
                # amiEvent.Phone = temp_data[1]
                # amiEvent.Extensions = temp_data[2]
                # amiEvent.DateReceived = temp_data[3]
                # amiEvent.StartTime = temp_data[4]
                # amiEvent.EndTime = temp_data[5]
                # amiEvent.CallStatus = temp_data[6]

        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 2,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus       
        }


    elif data.Event == 'incallStart': 
        event_api_type = 'start'

            # {"Token":"","UniqueId":"","Phone":"","CallType":"","Trunk":""}
            # elif temp_data[0] == 'incallStart':
            #     #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","CallType":"0","Trunk":"${ARG1}"})
            #     # same => n,Set(LisAMI_EventData="incall_start"&${CALLERID(num)}"&${ARG1})

            #     amiEvent =  AMIEvent(Event = 'incallStart')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Trunk = temp_data[2]

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'CallType' : 0,
        'Trunk' : data.Trunk
        }


    elif data.Event == 'incallDial': 
        event_api_type = 'dial'

                
            # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","Trunk":"","ExtTrunk":"","ExtPhone":""}

            # elif temp_data[0] == 'incallDial':
            #     # ;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${Exten}"],"CallType":"0","Trunk":"${ARG1}",
            # "ExtTrunk":"","ExtPhone":"${CALLERID(num)}"})
            #     #same => n,Set(LisAMI_EventData="incall_dial"&${CALLERID(num)}"&${ARG2}&${ARG1})

            #     amiEvent =  AMIEvent(Event = 'incallDial')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Trunk = temp_data[2]


        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 0,
        'Trunk' : data.Trunk,
        'ExtTrunk': '',
        'ExtPhone': data.Phone
        }


    elif data.Event == 'incallEnd': 
        event_api_type = 'hangup'
                
            # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","DateReceived":"","StartTime":"","EndTime":"","CallStatus":"","Trunk":""}

            # elif temp_data[0] == 'incallEnd':
            #     #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${CALLERID(num)}","Extensions":["${DIALEDPEERNUMBER}"],"CallType":"2","DateReceived":"${CDR(start)}",
            # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})
            #     #exten => s,1,Set(LisAMI_EventData=incallEnd_${CALLERID(num)}_${DIALEDPEERNUMBER}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${ARG1}_${DIALSTATUS})

            #     amiEvent =  AMIEvent(Event = 'incallEnd')

                # amiEvent.Uniqueid = message.Uniqueid
                # amiEvent.Phone = temp_data[1]
                # amiEvent.Extensions = temp_data[2]
                # amiEvent.DateReceived = temp_data[3]
                # amiEvent.StartTime = temp_data[4]
                # amiEvent.EndTime = temp_data[5]
                # amiEvent.Trunk = temp_data[6]
                # amiEvent.CallStatus = temp_data[7]


        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 0,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus,
        'Trunk' : data.Trunk    
        }


    elif data.Event == 'outcallDial': 
        event_api_type = 'dial'

            # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","Trunk":"","ExtTrunk":"","ExtPhone":""}

            # elif temp_data[0] == 'outcallDial':
            #     #;same => n,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${ARG2}","Extensions":["${CALLERID(num)}"],"CallType":"1","Trunk":"${ARG1}","ExtTrunk":"","ExtPhone":""})
            #     #same => n,Set(LisAMI_EventData="outcall_dial"&${ARG2}&${CALLERID(num)}"&${ARG1})

                
            #     amiEvent =  AMIEvent(Event = 'outcallDial')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Extensions = temp_data[2]
            #     amiEvent.Trunk = temp_data[3]

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 1,        
        'Trunk' : data.Trunk,
        'ExtTrunk': '',
        'ExtPhone' : ''
        }


    elif data.Event == 'outcallEnd': 
        event_api_type = 'hangup'

            # {"Token":"","UniqueId":"","Phone":"","Extensions":[""],"CallType":"","DateReceived":"","StartTime":"","EndTime":"","CallStatus":"","Trunk":""}

            # #;exten => s,33,Set(POST={"Token":"${ID_TOKEN}","UniqueId":"${UNIQUEID}","Phone":"${PhoneTo}","Extensions":["${CALLERID(num)}"],"CallType":"1","DateReceived":"${CDR(start)}",
            # "StartTime":"${CallStartDT}","EndTime":"${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}","CallStatus":"${CallStatus}","Trunk":"${ARG1}"})
            #     # exten => s,1,Set(LisAMI_EventData=outcallEnd_${PhoneTo}_${CALLERID(num)}_${CDR(start)}_${CallStartDT}_${STRFTIME(${EPOCH},,%Y-%m-%d %H:%M:%S)}_${ARG1}_${DIALSTATUS})

                
            #     amiEvent =  AMIEvent(Event = 'outcallEnd')

            #     amiEvent.Uniqueid = message.Uniqueid
            #     amiEvent.Phone = temp_data[1]
            #     amiEvent.Extensions = temp_data[2]
            #     amiEvent.DateReceived = temp_data[3]
            #     amiEvent.StartTime = temp_data[4]
            #     amiEvent.EndTime = temp_data[5]
            #     amiEvent.Trunk = temp_data[6]
            #     amiEvent.CallStatus = temp_data[7]
                

        CallStatus = ''
        if data.CallStatus == '':
            CallStatus = '1'
        elif data.CallStatus == 'CANCEL':
            CallStatus = '0'
        elif data.CallStatus == 'OTHER':
            CallStatus = '1'
        elif data.CallStatus == 'ANSWER':
            CallStatus = '2'
        elif data.CallStatus == 'TALK':
            CallStatus = '5'
        elif data.CallStatus == 'NOANSWER':
            CallStatus = '0'
        elif data.CallStatus == 'BUSY':
            CallStatus = '0'
        elif data.CallStatus == 'CONGESTION':
            CallStatus = '0'
        elif data.CallStatus == 'CHANUNAVAIL':
            CallStatus = '0'

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'Phone' : data.Phone,
        'Extensions' : data.Extensions,
        'CallType' : 1,
        'DateReceived': data.DateReceived,
        'StartTime' : data.StartTime,
        'EndTime': data.EndTime,
        'CallStatus' : CallStatus,
        'Trunk' : data.Trunk    
        }


    elif data.Event == 'BridgeEnter':
        event_api_type = 'bridge'

        # подготовка данных

        # {"Token":"","UniqueId":"","Channel":"","Exten":"","CallerIDNum":"","CallerIDName":"","ConnectedLineNum":"","ConnectedLineName":""}
        
        # {"Token":"375291448457","UniqueId":"1718883568.41","Channel":"SIP\/0001368-00000015","Exten":"3","CallerIDNum":"+375293193330","CallerIDName":"+375293193330","ConnectedLineNum":"152","ConnectedLineName":"<unknown>"}

        Body = {
        'Token' : UnitID,
        'UniqueId' : data.Uniqueid,
        'BridgeUniqueid' : data.BridgeUniqueid,
        'Channel' : data.Channel,
        'Exten' : data.Exten,
        'CallerIDNum' : data.CallerIDNum,
        'CallerIDName' : data.CallerIDName,
        'ConnectedLineNum' : data.ConnectedLineNum,
        'ConnectedLineName' : data.ConnectedLineName
        }


    if Body:

        # сериализуем данные для отправки (БЕЗ маркера - аутентичный вид)
        d1 = json.dumps(Body)

        print(event_api_type + ':\n' + d1+'\n')

        resTime = 0

        #print(apiAlternative2URL + event_api_type)
        #print(d1)

    
        # отправляем
        try:
            if pingState == 0:
                answer = ''

                start = time.time() ## точка отсчета времени

                answer = requests.post(apiAlternative2URL + event_api_type, data=d1, headers=headers, timeout=3, verify=False)
                #answer = requests.request("POST", apiAlternative2URL + event_api_type, data=d1, headers=headers, timeout=3)

                resTime = time.time() - start ## длительность запроса

                print(repr(answer) + ' - ' + repr(resTime) + ' s')

            else:
                answer = Response()
            


        except requests.Timeout:
            errorSend = 1
            errorText = 'Timeout'
            print('Timeout')
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Alternative2API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Timeout', response = '', responseTime = resTime)))


        except requests.ConnectionError as e:
            errorSend = 1
            errorText = 'ConnectionError' + repr(e)
            print(errorText)
            
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Alternative2API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = errorText, response = '', responseTime = resTime)))

        except BaseException as ex:

            errorSend = 1
            errorText = repr(ex)

            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

            stack_trace_string = ''
            for line in stack_trace:
                stack_trace_string += line + '\n'

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', "Exception message : %s" %ex_value + '   Stack trace: ' + stack_trace_string)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###  error post : %s" % stack_trace)

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Alternative2API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = 'Error '+repr(answer), response = answer.text, responseTime = resTime)))

        if pingState == 0:

            if answer:
                
                try:
                    ANSWERstring = answer.text.encode('ascii', 'ignore').decode('ascii')
                    print('answer:' + ANSWERstring)
                    # добавление в очередь на запись в БД
                    DbQueue.put(DbItem(DB_Type.Alternative2API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = repr(answer), response = ANSWERstring, responseTime = resTime)))

                except:
                    print('answer: answer convert error')
                    # добавление в очередь на запись в БД
                    DbQueue.put(DbItem(DB_Type.Alternative2API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = repr(answer), response = ANSWERstring, responseTime = resTime)))

                    

            else:
                # добавление в очередь на запись в БД
                DbQueue.put(DbItem(DB_Type.Alternative2API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = repr(answer), response = '', responseTime = resTime)))


            pass

        else:
            print('answer: None connection')
            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Alternative2API_Event, APIEvent(Uniqueid =data.Uniqueid, event = event_api_type, request = d1, status = '<Response [404]>', response = 'None connection (Not ping)', responseTime = resTime)))

            pass




# Метод для потока обновления времени в файле /etc/rc.local
def setTime_thread():
    global exiting, setTime_thread_timer

    #while not exiting:
    try:
        
        setTime()
        ping()  # проверка доступности интернет
            
        setTime_thread_timer = threading.Timer(60.0, setTime_thread)
        setTime_thread_timer.start()
        #time.sleep(60)          


    except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

            stack_trace_string = ''
            for line in stack_trace:
                stack_trace_string += line + '\n'

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', "Exception message : %s" %ex_value + '   Stack trace: ' + stack_trace_string)))
   





# тред и очередь сделаны для отправки POST запросов,
# чтобы от Asterisk данные принимать без задержек
def post_thread():
    global q, exiting

    while not exiting:
        #print('Post: ' + repr(q.qsize()))
        data = q.get()
        if (data == 'exit'): break
        try:
            #print(data)
            crm_post(data)
        
        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

            stack_trace_string = ''
            for line in stack_trace:
                stack_trace_string += line + '\n'

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', "Exception message : %s" %ex_value + '   Stack trace: ' + stack_trace_string)))
   

            #print("Exception type : %s " % ex_type.__name__)
            print("Exception message : %s" %ex_value)
            print("###  error post : %s" % stack_trace)



# тред и очередь сделаны для отправки POST запросов в альтернативную интеграцию,
def APIalternativePost_thread():
    global apiAlternativeQueue, exiting

    while not exiting:
        #print('Post: ' + repr(q.qsize()))
        data = apiAlternativeQueue.get()
        if (data == 'exit'): break
        try:
            #print(data)
            Alternative_crm_post(data)
        
        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

            stack_trace_string = ''
            for line in stack_trace:
                stack_trace_string += line + '\n'

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', "Exception message : %s" %ex_value + '   Stack trace: ' + stack_trace_string)))
   

            #print("Exception type : %s " % ex_type.__name__)
            print("Exception message : %s" %ex_value)
            print("###  error post : %s" % stack_trace)


# тред и очередь сделаны для отправки POST запросов во вторую альтернативную интеграцию,
def APIalternative2Post_thread():
    global apiAlternative2Queue, exiting

    while not exiting:
        #print('Post: ' + repr(q.qsize()))
        data = apiAlternative2Queue.get()
        if (data == 'exit'): break
        try:
            #print(data)
            Alternative2_crm_post(data)
        
        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

            stack_trace_string = ''
            for line in stack_trace:
                stack_trace_string += line + '\n'

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', "Exception message : %s" %ex_value + '   Stack trace: ' + stack_trace_string)))
   

            #print("Exception type : %s " % ex_type.__name__)
            print("Exception message : %s" %ex_value)
            print("###  error post : %s" % stack_trace)


# поток для записи данных в БД,
# чтобы от Asterisk данные принимать без задержек
def DB_Write_thread():
    global DbQueue, exiting, dateStart, os_name

    th_DB_Path = GetDBPath(os_name)

    # создаем объект SQLite
    th_connection = sqlite3.connect(th_DB_Path)
    th_cursor = th_connection.cursor()

    # бесконечный цикл
    while not exiting:
        
        if dateStart != datetime.now().strftime("%Y-%m-%d"):    # если дата изменилась
            #logging.info('Date chenge, create new BD: ' + dateStart + ' - ' + datetime.now().strftime("%Y-%m-%d"))
            th_DB_Path = GetDBPath(os_name)
            th_connection = sqlite3.connect(th_DB_Path)
            th_cursor = connection.cursor()
            


        #print('DB: ' + repr(DbQueue.qsize()))
        #if (DbQueue.qsize() > 10):
        #    pass
        #else:
        dbItem = DbQueue.get()
        if (dbItem == 'exit'): break
        
        try:
            Write_toDB(dbItem.type,th_cursor, th_connection, dbItem.data)  #type: DB_Type, cur, connection, data):
                #write_to_db_AMIEvent(th_cursor, th_connection, MessageData)
                                                                                         ##print(data) #crm_post(data)
        
        except BaseException as ex:
            # Get current system exception
            ex_type, ex_value, ex_traceback = sys.exc_info()

            # Extract unformatter stack traces as tuples
            trace_back = traceback.extract_tb(ex_traceback)

            # Format stacktrace
            stack_trace = list()

            for trace in trace_back:
                stack_trace.append("File : %s , Line : %d, Func.Name : %s, Message : %s" % (trace[0], trace[1], trace[2], trace[3]))

            stack_trace_string = ''
            for line in stack_trace:
                stack_trace_string += line + '\n'

            # добавление в очередь на запись в БД
            DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('Error', "Exception message : %s" %ex_value + '   Stack trace: ' + stack_trace_string)))
   

            #print("Exception type : %s " % ex_type.__name__)
            #print("Exception message : %s" %ex_value)
            print("###  error Write to BD : %s" % stack_trace)







# Запускаем потоки
if apiAlternative == 1:
    threading.Thread(target=APIalternativePost_thread, daemon=True).start()

if apiAlternative2 == 1:
    threading.Thread(target=APIalternative2Post_thread, daemon=True).start()

threading.Thread(target=post_thread, daemon=True).start()
threading.Thread(target=DB_Write_thread, daemon=True).start()
setTime_thread()
#threading.Thread(target=setTime_thread, daemon=True).start()


def main():
    global exiting


    manager.connect()
    try: manager.loop.run_forever()
    except KeyboardInterrupt:
        exiting = True
        # добавление в очередь на запись в БД
        DbQueue.put(DbItem(DB_Type.Script_Event, ScriptEvent('STOP', 'Script STOP')))   
        q.put('exit')
        DbQueue.put('exit')

        if apiAlternative == 1:
            apiAlternativeQueue.put('exit')

        if apiAlternative2 == 1:
            apiAlternative2Queue.put('exit')

        setTime_thread_timer.cancel()        
        manager.loop.close() 


if __name__ == '__main__': 
     main()





