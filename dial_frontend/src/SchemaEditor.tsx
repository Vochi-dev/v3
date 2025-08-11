import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import ReactFlow, {
    applyNodeChanges,
    applyEdgeChanges,
    addEdge,
    Node,
    Edge,
    OnNodesChange,
    OnEdgesChange,
    OnConnect,
    Connection,
    ReactFlowInstance,
    ReactFlowProvider
} from 'reactflow';
import 'reactflow/dist/style.css';
import './SchemaEditor.css';
import IncomingCallNode from './nodes/IncomingCallNode';
import OutgoingCallNode from './nodes/OutgoingCallNode';
import GenericNode from './nodes/GenericNode';
import ExternalLinesNode from './nodes/ExternalLinesNode';
import DialNode from './nodes/DialNode';
import GreetingNode from './nodes/GreetingNode';
// ИЗМЕНЕНИЕ: ManagerInfo теперь импортируется из единого источника.
import { Schema, Line, ManagerInfo } from './types';
import IncomingCallModal from './IncomingCallModal';
import NodeActionModal from './NodeActionModal';
import DialModal from './DialModal';
import AddManagerModal from './AddManagerModal';
import GreetingModal from './GreetingModal';
import WorkScheduleModal, { SchedulePeriod } from './WorkScheduleModal';
import { NodeType, getNodeRule } from './nodeRules';
import OutgoingCallModal from './OutgoingCallModal';
import PatternCheckModal from './PatternCheckModal';
import ExternalNumberModal from './ExternalNumberModal';

// ИЗМЕНЕНИЕ: Локальный интерфейс удален.

interface SchemaEditorProps {
    enterpriseId: string;
    schema: Partial<Schema>;
    onSave: (schema: Partial<Schema>) => Promise<Schema>;
    onCancel: () => void;
    onDelete: (schemaId: string) => void;
}

interface SchemaEditorWithProviderProps extends SchemaEditorProps {
    schemaType?: 'incoming' | 'outgoing';
}

const nodeTypes = {
    [NodeType.Start]: IncomingCallNode,
    'outgoing-call': OutgoingCallNode,
    [NodeType.Greeting]: GreetingNode,
    [NodeType.Dial]: DialNode,
    [NodeType.WorkSchedule]: GenericNode,
    [NodeType.PatternCheck]: GenericNode,
    [NodeType.IVR]: GenericNode,
    'externalLines': ExternalLinesNode,
};

const DAYS_OF_WEEK_ORDER = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];

const formatDays = (days: Set<string> | string[]): string => {
    const daysSet = new Set(days);
    if (!daysSet || daysSet.size === 0) return '';
    
    const sortedDays = DAYS_OF_WEEK_ORDER.filter(day => daysSet.has(day));
    if (sortedDays.length === 0) return '';

    const ranges: string[] = [];
    let startRange = sortedDays[0];

    for (let i = 1; i <= sortedDays.length; i++) {
        const dayIndex = DAYS_OF_WEEK_ORDER.indexOf(sortedDays[i]);
        const prevDayIndex = DAYS_OF_WEEK_ORDER.indexOf(sortedDays[i - 1]);

        if (i === sortedDays.length || dayIndex !== prevDayIndex + 1) {
            const endRange = sortedDays[i - 1];
            if (startRange === endRange) {
                ranges.push(startRange);
            } else {
                ranges.push(`${startRange}-${endRange}`);
            }
            if (i < sortedDays.length) {
                startRange = sortedDays[i];
            }
        }
    }
    return ranges.join(', ');
};

const SchemaEditor: React.FC<SchemaEditorWithProviderProps> = (props) => {
    const { enterpriseId, schema, onSave, onCancel, onDelete, schemaType = 'incoming' } = props;
    const [nodes, setNodes] = useState<Node[]>(schema.schema_data?.nodes || []);
    const [edges, setEdges] = useState<Edge[]>(schema.schema_data?.edges || []);
    const [schemaName, setSchemaName] = useState(schema.schema_name || 'Новая схема');
    const [isLinesModalOpen, setIsLinesModalOpen] = useState(false);
    // Умная переадресация (для входящих схем)
    const [smartRedirect, setSmartRedirect] = useState<boolean>(Boolean((schema.schema_data as any)?.smartRedirect));
    const [isNodeActionModalOpen, setIsNodeActionModalOpen] = useState(false);
    const [isDialModalOpen, setIsDialModalOpen] = useState(false);
    const [isAddManagerModalOpen, setIsAddManagerModalOpen] = useState(false);
    const [isGreetingModalOpen, setIsGreetingModalOpen] = useState(false);
    const [isWorkScheduleModalOpen, setIsWorkScheduleModalOpen] = useState(false);
    const [isOutgoingCallModalOpen, setIsOutgoingCallModalOpen] = useState(false);
    const [isPatternCheckModalOpen, setIsPatternCheckModalOpen] = useState(false);
    const [isExternalNumberModalOpen, setIsExternalNumberModalOpen] = useState(false);
    const [dialManagers, setDialManagers] = useState<ManagerInfo[]>([]);
    const [editingNode, setEditingNode] = useState<Node | null>(null);
    const [newNodeId, setNewNodeId] = useState<string | null>(null);
    
    const [sourceNodeForAction, setSourceNodeForAction] = useState<{node: Node, type: NodeType} | null>(null);

    const [selectedLines, setSelectedLines] = useState<Set<string>>(new Set());
    const [allLines, setAllLines] = useState<Line[]>([]);
    const [isLoading, setIsLoading] = useState(false);

    const reactFlowWrapper = useRef<HTMLDivElement>(null);
    const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null);

    const isOutgoingSchema = useMemo(() => schema.schema_name?.startsWith('Исходящая'), [schema.schema_name]);

    const startNode = useMemo(() => nodes.find(node => node.type === 'start' || node.type === NodeType.Start), [nodes]);
    const outgoingStartNode = useMemo(() => nodes.find(node => node.id === 'start-outgoing'), [nodes]);

    const hasAssignedLines = useMemo(() => {
        if (isOutgoingSchema) {
            return (outgoingStartNode?.data?.phones?.length > 0);
        }
        return (startNode?.data?.assignedLines?.length > 0) || (selectedLines.size > 0);
    }, [isOutgoingSchema, startNode, outgoingStartNode, selectedLines]);

    const handleDeleteClick = () => {
        if (hasAssignedLines) {
            if (isOutgoingSchema) {
                alert("Нельзя удалить схему, пока к ней привязаны менеджеры. Сначала отвяжите их в узле 'Исходящий звонок'.");
            } else {
                alert("Нельзя удалить схему, пока к ней привязаны линии. Сначала отвяжите их в узле 'Поступил новый звонок'.");
            }
            return;
        }

        if (schema.schema_id) {
            onDelete(schema.schema_id);
        } else {
            onCancel();
        }
    };

    const handleDeleteNode = () => {
        const nodeToDelete = editingNode || (sourceNodeForAction ? sourceNodeForAction.node : null);
        if (!nodeToDelete) return;

        // --- НАЧАЛО: Логика восстановления плюса ---
        // Если удаляем узел "Внешние линии", находим его родителя и возвращаем ему "+"
        if (nodeToDelete.data.label === 'Внешние линии') {
            const parentEdge = edges.find(e => e.target === nodeToDelete.id);
            if (parentEdge) {
                setNodes(nds => nds.map(n => 
                    n.id === parentEdge.source 
                    ? { ...n, data: { ...n.data, onAddClick: handleAddNodeClick } } 
                    : n
                ));
            }
        }
        // --- КОНЕЦ: Логика восстановления плюса ---

        if (nodeToDelete.id === '1' && schemaType !== 'outgoing') {
            alert("Стартовый узел 'Входящий звонок' удалить нельзя.");
            return;
        }

        const idsToDelete = new Set<string>();
        const queue: string[] = [nodeToDelete.id];
        idsToDelete.add(nodeToDelete.id);

        while (queue.length > 0) {
            const currentId = queue.shift()!;
            const childrenEdges = edges.filter(edge => edge.source === currentId);

            for (const edge of childrenEdges) {
                if (!idsToDelete.has(edge.target)) {
                    idsToDelete.add(edge.target);
                    queue.push(edge.target);
                }
            }
        }

        setNodes(nds => nds.filter(n => !idsToDelete.has(n.id)));
        setEdges(eds => eds.filter(e => !idsToDelete.has(e.source) && !idsToDelete.has(e.target)));
        
        handleCloseModals();
    };

    useEffect(() => {
        if (isOutgoingSchema) return;

        fetch(`/dial/api/enterprises/${enterpriseId}/lines`)
            .then(res => res.ok ? res.json() : Promise.reject(res))
            .then((data: Line[]) => {
                setAllLines(data);
                // Если мы редактируем существующую схему, подставляем ее линии
                if (schema.schema_id) {
                    const normalizedSchemaName = schema.schema_name?.trim().toLowerCase();
                    const initiallySelected = new Set(
                        data.filter(line => line.in_schema && line.in_schema.trim().toLowerCase() === normalizedSchemaName).map(line => line.id)
                    );
                    setSelectedLines(initiallySelected);
                } else {
                    // Для новой схемы начинаем с пустого сета
                    setSelectedLines(new Set());
                }
            })
            .catch(err => console.error("Не удалось загрузить линии для схемы", err));
    }, [schema.schema_id, schema.schema_name, enterpriseId, isOutgoingSchema]);


    useEffect(() => {
        setNodes(schema.schema_data?.nodes || []);
        setEdges(schema.schema_data?.edges || []);
        setSchemaName(schema.schema_name || 'Новая схема');
        setSmartRedirect(Boolean((schema.schema_data as any)?.smartRedirect));
        
        if (reactFlowInstance && schema.schema_data?.viewport) {
            const { x, y, zoom } = schema.schema_data.viewport;
            reactFlowInstance.setViewport({ x, y, zoom });
        }
    }, [schema, reactFlowInstance]);


    const onNodesChange: OnNodesChange = useCallback((changes) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
    const onEdgesChange: OnEdgesChange = useCallback((changes) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);
    const onConnect: OnConnect = useCallback((params: Edge | Connection) => setEdges((eds) => addEdge(params, eds)), []);

    const handleSave = async () => {
        if (!schemaName.trim()) {
            alert('Название схемы не может быть пустым.');
            return;
        }
        setIsLoading(true);
        const viewport = reactFlowInstance?.getViewport() || { x: 0, y: 0, zoom: 1 };
        const schemaToSave: Partial<Schema> = {
            ...schema,
            enterprise_id: enterpriseId,
            schema_name: schemaName,
            schema_data: { nodes, edges, viewport, smartRedirect },
            schema_type: schemaType,
        };

        try {
            const savedSchema = await onSave(schemaToSave);
            
            if (savedSchema && savedSchema.schema_id) {
                // Блок для ВХОДЯЩИХ схем
                if (!isOutgoingSchema) {
                    const res = await fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${savedSchema.schema_id}/assign_lines`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(Array.from(selectedLines)),
                    });

                    if (!res.ok) {
                       throw new Error('Ошибка привязки линий');
                    }
                } else { // Блок для ИСХОДЯЩИХ схем
                    const outgoingNode = nodes.find(n => n.type === 'outgoing-call');
                    const phoneIds = outgoingNode?.data?.phones || [];

                    const res = await fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${savedSchema.schema_id}/assign_phones`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(phoneIds),
                    });

                    if (!res.ok) {
                        throw new Error('Ошибка привязки менеджеров к исходящей схеме');
                    }
                }
            }
            // >>> НАЧАЛО: Вызов сервиса генерации конфига
            alert('Схема успешно сохранена!');
            
            // Запускаем генерацию конфига для ЛЮБОЙ схемы (и входящей, и исходящей)
            try {
                const planResponse = await fetch('/plan/generate_config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ 
                        enterprise_id: enterpriseId,
                        schema_id: savedSchema.schema_id // Передаем ID сохраненной схемы
                    }),
                });

                if (!planResponse.ok) {
                    const errorData = await planResponse.json();
                    throw new Error(errorData.detail || 'Failed to generate plan config');
                }

                const planResult = await planResponse.json();
                console.log('Plan generation result:', planResult.message);

            } catch (error: any) {
                // Уведомляем пользователя об ошибке, но не прерываем основной процесс
                console.error('Error generating plan config:', error);
                alert(`Не удалось сгенерировать конфигурационный файл: ${error.message}`);
            }
            // <<< КОНЕЦ: Вызов сервиса генерации конфига
            onCancel();
        } catch (error: any) {
            console.error("Failed to save schema or assign lines:", error);
            alert(`Не удалось сохранить схему или привязать линии: ${error.message}`);
        } finally {
            setIsLoading(false);
        }
    };

    const handleNodeClick = (event: React.MouseEvent, node: Node) => {
        event.stopPropagation();
        setEditingNode(node);

        switch(node.type) {
            case 'outgoing-call':
                setIsOutgoingCallModalOpen(true);
                break;
            case NodeType.Start:
                setIsLinesModalOpen(true);
                break;
            case 'externalLines':
                setIsExternalNumberModalOpen(true);
                break;
            case NodeType.Greeting:
                if (isOutgoingSchema) {
                    // ИСПРАВЛЕНИЕ: Открываем модалку, если это узел "Внешние линии"
                    if (node.data.label === 'Внешние линии') {
                        setIsExternalNumberModalOpen(true);
                    }
                    // Для остальных (Life, MTS) - ничего не делаем, как и договаривались
                    return;
                }
                setIsGreetingModalOpen(true);
                break;
            case NodeType.Dial:
                setDialManagers(node.data.managers || []);
                setIsDialModalOpen(true);
                break;
            case NodeType.WorkSchedule:
                setIsWorkScheduleModalOpen(true);
                break;
            case NodeType.PatternCheck:
                setIsPatternCheckModalOpen(true);
                break;
            default:
                setEditingNode(null);
                break;
        }
    };

    const handleAddNodeClick = (nodeId: string) => {
        const sourceNode = nodes.find(n => n.id === nodeId);
        if (!sourceNode) return;

        if (isOutgoingSchema && sourceNode.type === NodeType.Greeting) {
            
            // ИСПРАВЛЕНИЕ: Используем Date.now() для гарантированно уникального ID
            const newNodeId = Date.now().toString();

            const newNode: Node = {
                id: newNodeId,
                type: 'externalLines',
                position: {
                    x: sourceNode.position.x,
                    y: sourceNode.position.y + 150,
                },
                data: {
                    label: 'Внешние линии',
                    external_lines: [],
                    onAddClick: undefined 
                },
            };

            const newEdge: Edge = {
                id: `e${sourceNode.id}-${newNodeId}`,
                source: sourceNode.id,
                target: newNodeId,
            };

            updateNodeData(sourceNode.id, { ...sourceNode.data, onAddClick: undefined });
            setNodes(nds => [...nds, newNode]);
            setEdges(eds => [...eds, newEdge]);
            
            setEditingNode(newNode);
            setIsExternalNumberModalOpen(true);

            return;
        }

        setSourceNodeForAction({ node: sourceNode, type: sourceNode.type as NodeType });
        setIsNodeActionModalOpen(true);
    };

    const handleAddPatternCheckNode = (sourceNodeId: string) => {
        const sourceNode = nodes.find(n => n.id === sourceNodeId);
        if (sourceNode) {
            setSourceNodeForAction({ node: sourceNode, type: sourceNode.type as NodeType });
            setIsPatternCheckModalOpen(true);
        }
    };

    const createNewNode = (nodeType: NodeType, data: any) => {
        if (!sourceNodeForAction) return;

        const rule = getNodeRule(nodeType);
        if (!rule) return;

        const newNodeId = (Math.max(0, ...nodes.map(n => parseInt(n.id, 10))) + 1).toString();
        
        const newNode: Node = {
            id: newNodeId,
            type: nodeType,
            position: {
                x: sourceNodeForAction.node.position.x,
                y: sourceNodeForAction.node.position.y + 150,
            },
            data: { 
                label: rule.name,
                onAddClick: handleAddNodeClick,
                ...data 
            },
        };

        const newEdge: Edge = {
            id: `e${sourceNodeForAction.node.id}-${newNodeId}`,
            source: sourceNodeForAction.node.id,
            target: newNodeId,
        };

        setNodes(nds => [...nds, newNode]);
        setEdges(eds => [...eds, newEdge]);
        setSourceNodeForAction(null);
    };

    const handleOpenModalFor = (type: NodeType) => {
        console.log(`--- handleOpenModalFor ЗАПУЩЕНА для типа: ${type} ---`);
        if (!sourceNodeForAction) {
            console.error("ОШИБКА: sourceNodeForAction не определен. Невозможно создать узел.");
            return;
        }

        const parentNode = sourceNodeForAction.node;
        const rule = getNodeRule(type)!;
        
        console.log("Родительский узел:", parentNode);

        // --- НАЧАЛО: Улучшенная логика расчета позиции ---
        const margin = 50; // Пространство между узлами

        // Находим все существующие дочерние узлы родителя (сиблинги для нового узла)
        const siblingEdges = edges.filter(e => e.source === parentNode.id);
        const siblingNodes = siblingEdges
            .map(e => nodes.find(n => n.id === e.target))
            .filter((n): n is Node => !!n);

        let yPos;

        if (siblingNodes.length > 0) {
            // Если сиблинги есть, находим самую нижнюю точку среди них
            const lowestPoint = siblingNodes.reduce((maxBottom, node) => {
                // Используем реальную высоту узла, если она доступна, или запасное значение 150
                const nodeBottom = node.position.y + (node.height || 150);
                return Math.max(maxBottom, nodeBottom);
            }, -Infinity); // Начинаем с минус бесконечности для корректного сравнения
            
            yPos = lowestPoint + margin;
        } else {
            // Если сиблингов нет, позиционируем узел под родителем
            const parentBottom = parentNode.position.y + (parentNode.height || 75); // Запасная высота для родителя 75
            yPos = parentBottom + margin;
        }
        // --- КОНЕЦ: Улучшенная логика расчета позиции ---
        
        // 1. Определяем ID и базовые данные для нового узла
        const newNodeId = (Math.max(0, ...nodes.map(n => parseInt(n.id, 10))) + 1).toString();
        let nodeData: any = { 
            label: rule.name,
            onAddClick: handleAddNodeClick,
        };
        let initialManagers: ManagerInfo[] = [];

        // ИСПРАВЛЕНИЕ: Добавлена проверка на isOutgoingSchema, чтобы в исходящей схеме
        // после "Звонка на список" создавался специальный узел "Внешние линии"
        if (isOutgoingSchema && type === NodeType.Greeting && sourceNodeForAction.type === NodeType.Dial) {
             nodeData = {
                label: 'Внешние линии',
                external_lines: [],
                onAddClick: undefined // У этого узла нет кнопки "+"
            };
        }

        // Логика наследования для узла "Звонок на список"
        if (type === NodeType.Dial && parentNode.type === NodeType.Dial) {
            const parentData = parentNode.data;
            if (parentData.managers) {
                initialManagers = parentData.managers.filter((m: ManagerInfo) => m.phone && m.phone.length <= 4);
            }
            if (parentData.holdMusic) {
                nodeData.holdMusic = parentData.holdMusic;
            }
        }

        // 3. Создаем новый узел
        const newNode: Node = {
            id: newNodeId,
            type: type,
            position: {
                x: parentNode.position.x,
                y: yPos, // Используем вычисленную позицию
            },
            data: nodeData,
        };
        console.log("СОЗДАН НОВЫЙ УЗЕЛ:", newNode);

        // 4. Создаем связь
        const newEdge: Edge = {
            id: `e${parentNode.id}-${newNodeId}`,
            source: parentNode.id,
            target: newNodeId,
        };
        console.log("СОЗДАНА НОВАЯ СВЯЗЬ:", newEdge);
        
        // 5. Обновляем состояние
        console.log("ОБНОВЛЕНИЕ СОСТОЯНИЯ: удаляем '+' у родителя, добавляем узел и связь.");
        updateNodeData(parentNode.id, { ...parentNode.data, onAddClick: undefined });
        setNodes(nds => [...nds, newNode]);
        setEdges(eds => [...eds, newEdge]);
        
        // 6. Открываем соответствующую модалку для нового узла
        console.log(`ОТКРЫТИЕ МОДАЛЬНОГО ОКНА для узла ${newNode.id}`);
        setEditingNode(newNode);
        setNewNodeId(newNode.id);
        setIsNodeActionModalOpen(false);
        switch (type) {
            case NodeType.Dial:
                setDialManagers(initialManagers);
                setIsDialModalOpen(true);
                break;
            case NodeType.Greeting:
                if (newNode.data.label === 'Внешние линии') {
                    console.log("Открываем ExternalNumberModal");
                    setIsExternalNumberModalOpen(true);
                } else {
                    console.log("Открываем GreetingModal");
                    setIsGreetingModalOpen(true);
                }
                break;
            case NodeType.WorkSchedule:
                setIsWorkScheduleModalOpen(true);
                break;
        }
    };
    
    const handleCancelNodeCreation = () => {
        if (newNodeId) {
            setNodes(nds => nds.filter(n => n.id !== newNodeId));
            setEdges(eds => eds.filter(e => e.target !== newNodeId));
            
            // Восстанавливаем "+" у родителя
            const edge = edges.find(e => e.target === newNodeId);
            if (edge) {
                setNodes(nds => nds.map(n => 
                    n.id === edge.source 
                    ? { ...n, data: { ...n.data, onAddClick: handleAddNodeClick } } 
                    : n
                ));
            }
        }
        handleCloseModals();
    };

    const updateNodeData = (nodeId: string, data: any) => {
        setNodes((nds) =>
            nds.map((node) => {
                if (node.id === nodeId) {
                    return { ...node, data: { ...node.data, ...data } };
                }
                return node;
            })
        );
    };

    const handleConfirmWorkSchedule = (periods: SchedulePeriod[]) => {
        const parentNode = editingNode;
        if (!parentNode) return;

        // 1. Создаем Set с лейблами для НОВОГО состояния (из модального окна)
        const newLabels = new Set(periods.map(period => {
            const daysLabel = formatDays(period.days);
            return `${daysLabel} ${period.startTime}-${period.endTime}`;
        }));
        newLabels.add('Остальное время');

        // 2. Находим СТАРЫХ непосредственных детей и создаем карту "лейбл -> узел"
        const oldChildEdges = edges.filter(e => e.source === parentNode.id);
        const oldChildren = oldChildEdges.map(edge => nodes.find(n => n.id === edge.target)).filter((n): n is Node => !!n);
        const oldChildrenMap = new Map(oldChildren.map(n => [n.data.label, n]));

        // 3. Определяем, какие ветки нужно удалить
        const labelsToDelete = [...oldChildrenMap.keys()].filter(label => !newLabels.has(label));
        const nodesToDelete = labelsToDelete.map(label => oldChildrenMap.get(label)!).filter(Boolean);

        // 4. Рекурсивно собираем ID всех узлов в удаляемых ветках
        const idsToDelete = new Set<string>();
        const queue: Node[] = [...nodesToDelete];
        nodesToDelete.forEach(n => idsToDelete.add(n.id));

        while (queue.length > 0) {
            const current = queue.shift()!;
            const childrenOfCurrent = edges
                .filter(e => e.source === current.id)
                .map(e => nodes.find(n => n.id === e.target))
                .filter((n): n is Node => !!n);

            for (const child of childrenOfCurrent) {
                if (!idsToDelete.has(child.id)) {
                    idsToDelete.add(child.id);
                    queue.push(child);
                }
            }
        }

        // 5. Фильтруем узлы и связи, удаляя помеченные ветки
        let finalNodes = nodes.filter(n => !idsToDelete.has(n.id));
        let finalEdges = edges.filter(e => !idsToDelete.has(e.source) && !idsToDelete.has(e.target));

        // 6. Обновляем данные в самом узле "График работы"
        finalNodes = finalNodes.map(n => 
            n.id === parentNode.id 
            ? { ...n, data: { ...parentNode.data, periods: periods.map(p => ({ ...p, days: Array.from(p.days) })) } } 
            : n
        );

        // 7. Готовимся к перерисовке/добавлению дочерних узлов
        const preservedChildren = oldChildren.filter(child => newLabels.has(child.data.label));
        const allFinalChildrenLabels = periods.map(p => `${formatDays(p.days)} ${p.startTime}-${p.endTime}`);
        allFinalChildrenLabels.push('Остальное время');

        let lastNodeId = Math.max(0, ...nodes.map(n => parseInt(n.id, 10) || 0));
        const parentPos = parentNode.position;
        const horizontalSpacing = 280;
        const verticalSpacing = 120;
        const totalWidth = horizontalSpacing * allFinalChildrenLabels.length;
        const startX = parentPos.x - totalWidth / 2 + horizontalSpacing / 2;
        
        const preservedChildrenMap = new Map(preservedChildren.map(n => [n.data.label, n]));
        const nodesToAdd: Node[] = [];
        const edgesToAdd: Edge[] = [];
        
        // 8. Проходим по ВСЕМ финальным лейблам, чтобы расставить узлы по порядку
        allFinalChildrenLabels.forEach((label, index) => {
            const existingChild = preservedChildrenMap.get(label);
            const newPosition = { x: startX + index * horizontalSpacing, y: parentPos.y + verticalSpacing };

            if (existingChild) {
                // Если узел уже есть (сохранился), просто обновляем его позицию
                finalNodes = finalNodes.map(n => n.id === existingChild.id ? { ...n, position: newPosition } : n);
            } else {
                // Если узла нет, создаем новый
                lastNodeId++;
                const newNodeId = lastNodeId.toString();
                const newNode: Node = {
                    id: newNodeId,
                    type: NodeType.IVR,
                    position: newPosition,
                    data: { label, onAddClick: handleAddNodeClick, isSingleOutput: true },
                };
                nodesToAdd.push(newNode);
                edgesToAdd.push({ id: `e${parentNode.id}-${newNodeId}`, source: parentNode.id, target: newNodeId });
            }
        });

        // 9. Устанавливаем финальное состояние
        setNodes([...finalNodes, ...nodesToAdd]);
        setEdges([...finalEdges, ...edgesToAdd]);

        // 10. Закрываем модальное окно
        setIsWorkScheduleModalOpen(false);
        setEditingNode(null);
    };

    const handleConfirmGreeting = (greetingData: any) => {
        if (editingNode) {
            updateNodeData(editingNode.id, { greetingFile: greetingData.greetingFile });
        } else {
            createNewNode(NodeType.Greeting, { greetingFile: greetingData.greetingFile });
        }
        setIsGreetingModalOpen(false);
        setEditingNode(null);
        setNewNodeId(null);
    };

    const handlePatternCheckConfirm = (patterns: { name: string }[]) => {
        const parentNode = editingNode || (sourceNodeForAction ? sourceNodeForAction.node : null);

        if (!parentNode) {
            console.error("No source or editing node defined for pattern check");
            setIsPatternCheckModalOpen(false);
            return;
        }

        if (sourceNodeForAction) {
            let lastNodeId = Math.max(0, ...nodes.map(n => parseInt(n.id, 10) || 0));
            
            lastNodeId++;
            const patternCheckNodeId = lastNodeId.toString();
            const patternCheckNode: Node = {
                id: patternCheckNodeId,
                type: NodeType.PatternCheck,
                position: { x: parentNode.position.x, y: parentNode.position.y + 150 },
                data: { label: 'Проверка по шаблону', patterns, onNodeClick: handleNodeClick },
            };

            const childrenResult = createPatternChildren(patternCheckNode, patterns, lastNodeId);
            const childNodes = childrenResult.nodes;
            const childEdges = childrenResult.edges;
            lastNodeId = childrenResult.lastId;

            const edgeToPatternCheck: Edge = {
                id: `e${parentNode.id}-${patternCheckNodeId}`,
                source: parentNode.id,
                target: patternCheckNodeId,
            };

            setNodes(nds => 
                nds.map(n => n.id === parentNode.id ? { ...n, data: { ...n.data, onAddClick: undefined } } : n)
                   .concat(patternCheckNode, ...childNodes)
            );
            setEdges(eds => eds.concat(edgeToPatternCheck, ...childEdges));
        
        } else if (editingNode) {
            const newPatternNames = new Set(patterns.map(p => p.name));
    
            const oldChildEdges = edges.filter(e => e.source === editingNode.id);
            const oldChildren = oldChildEdges.map(edge => nodes.find(n => n.id === edge.target)).filter((n): n is Node => !!n);
            const oldChildrenMap = new Map(oldChildren.map(n => [n.data.label, n]));
    
            const namesToDelete = [...oldChildrenMap.keys()].filter(name => !newPatternNames.has(name));
            const nodesToDelete = namesToDelete.map(name => oldChildrenMap.get(name)!).filter(Boolean);
    
            const idsToDelete = new Set<string>();
            const queue: Node[] = [...nodesToDelete];
            nodesToDelete.forEach(n => idsToDelete.add(n.id));
    
            while (queue.length > 0) {
                const current = queue.shift()!;
                const childrenOfCurrent = edges
                    .filter(e => e.source === current.id)
                    .map(e => nodes.find(n => n.id === e.target))
                    .filter((n): n is Node => !!n);
    
                for (const child of childrenOfCurrent) {
                    if (!idsToDelete.has(child.id)) {
                        idsToDelete.add(child.id);
                        queue.push(child);
                    }
                }
            }
    
            let finalNodes = nodes.filter(n => !idsToDelete.has(n.id));
            let finalEdges = edges.filter(e => !idsToDelete.has(e.source) && !idsToDelete.has(e.target));
    
            finalNodes = finalNodes.map(n => 
                n.id === editingNode.id 
                ? { ...n, data: { ...editingNode.data, patterns } } 
                : n
            );
    
            const preservedChildren = oldChildren.filter(child => newPatternNames.has(child.data.label));
            
            let lastNodeId = Math.max(0, ...finalNodes.map(n => parseInt(n.id, 10) || 0));
            const parentPos = editingNode.position;
            const horizontalSpacing = 300;
            const verticalSpacing = 120;
            const totalWidth = horizontalSpacing * patterns.length;
            const startX = parentPos.x - totalWidth / 2 + horizontalSpacing / 2;
            
            const preservedChildrenMap = new Map(preservedChildren.map(n => [n.data.label, n]));
            const nodesToAdd: Node[] = [];
            const edgesToAdd: Edge[] = [];
            
            patterns.forEach((pattern, index) => {
                const existingChild = preservedChildrenMap.get(pattern.name);
                const newPosition = { x: startX + index * horizontalSpacing, y: parentPos.y + verticalSpacing };
    
                if (existingChild) {
                    finalNodes = finalNodes.map(n => n.id === existingChild.id ? { ...n, position: newPosition } : n);
                } else {
                    lastNodeId++;
                    const newNodeId = lastNodeId.toString();
                    const newNode: Node = {
                        id: newNodeId,
                        type: NodeType.Greeting,
                        position: newPosition,
                        data: { label: pattern.name, onAddClick: handleAddNodeClick },
                    };
                    nodesToAdd.push(newNode);
                    edgesToAdd.push({ id: `e${editingNode.id}-${newNodeId}`, source: editingNode.id, target: newNodeId });
                }
            });
    
            setNodes([...finalNodes, ...nodesToAdd]);
            setEdges([...finalEdges, ...edgesToAdd]);
        }

        handleCloseModals();
    };

    const createPatternChildren = (parentNode: Node, patterns: { name: string }[], startingId: number) => {
        const newNodes: Node[] = [];
        const newEdges: Edge[] = [];
        let lastNodeId = startingId;
        
        const parentPosition = parentNode.position;
        const horizontalSpacing = 300;
        const verticalSpacing = 120;
        const totalWidth = horizontalSpacing * patterns.length;
        const startX = parentPosition.x - totalWidth / 2 + horizontalSpacing / 2;

        patterns.forEach((pattern, index) => {
            lastNodeId++;
            const childNodeId = lastNodeId.toString();

            const childNode: Node = {
                id: childNodeId,
                type: NodeType.Greeting,
                position: {
                    x: startX + index * horizontalSpacing,
                    y: parentPosition.y + verticalSpacing
                },
                data: {
                    label: pattern.name || 'Новая ветка',
                    onAddClick: handleAddNodeClick
                }
            };
            newNodes.push(childNode);

            const childEdge: Edge = {
                id: `e${parentNode.id}-${childNodeId}`,
                source: parentNode.id,
                target: childNodeId,
            };
            newEdges.push(childEdge);
        });

        return { nodes: newNodes, edges: newEdges, lastId: lastNodeId };
    };

    const handleOutgoingNodeConfirm = (nodeId: string, data: any) => {
        updateNodeData(nodeId, data);
        handleCloseModals();
    };

    const handleConfirmDial = (dialData: any) => {
        const finalData = { ...dialData, managers: dialManagers };
        if (editingNode) {
            updateNodeData(editingNode.id, finalData);
        } else {
            createNewNode(NodeType.Dial, finalData);
        }
        setIsDialModalOpen(false);
        setEditingNode(null);
        setNewNodeId(null);
        setDialManagers([]);
    };

    const handleCloseModals = () => {
        setIsDialModalOpen(false);
        setIsGreetingModalOpen(false);
        setIsWorkScheduleModalOpen(false);
        setIsLinesModalOpen(false);
        setIsOutgoingCallModalOpen(false);
        setIsPatternCheckModalOpen(false);
        setIsExternalNumberModalOpen(false);
        setEditingNode(null);
        setDialManagers([]);
        setSourceNodeForAction(null);
        setNewNodeId(null);
    };

    const handleOpenAddManagerModal = () => {
        setIsAddManagerModalOpen(true);
    };
    
    const handleAddManagers = (selectedManagers: ManagerInfo[]) => {
        setDialManagers(selectedManagers);
        setIsAddManagerModalOpen(false);
    };

    const handleRemoveManager = (indexToRemove: number) => {
        setDialManagers(prev => prev.filter((_, index) => index !== indexToRemove));
    };

    const handleLinesUpdate = async (newSelectedLines: Set<string>) => {
        setSelectedLines(newSelectedLines);
        setIsLinesModalOpen(false);

        // Немедленно отправляем изменения на сервер, если схема уже существует
        if (schema.schema_id) {
            setIsLoading(true);
            try {
                const res = await fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schema.schema_id}/assign_lines`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Array.from(newSelectedLines)),
                });

                if (!res.ok) {
                    const errorData = await res.json().catch(() => ({ detail: 'Неизвестная ошибка сервера' }));
                    throw new Error(errorData.detail || 'Не удалось обновить привязки линий');
                }
            } catch (error) {
                console.error("Failed to update line assignments:", error);
                alert(error instanceof Error ? error.message : 'Произошла ошибка');
                // Если произошла ошибка, возвращаем состояние к исходному, чтобы избежать рассинхронизации
                
                // Для этого нам нужно знать, какими были линии ДО открытия модалки.
                // К сожалению, в текущей реализации у нас нет прямого доступа к `initialSelectedLines` из `IncomingCallModal`.
                // Самый простой способ - перезагрузить данные.
                // TODO: Передать initialSelectedLines в handleLinesUpdate в будущем.
                window.location.reload(); 
            } finally {
                setIsLoading(false);
            }
        }
    };

    const assignedLines = useMemo(() => {
        return allLines.filter(line => selectedLines.has(line.id));
    }, [allLines, selectedLines]);

    const outgoingCallNode = useMemo(() => {
        if (!isOutgoingSchema) return null;
        return nodes.find(n => n.type === 'outgoing-call');
    }, [nodes, isOutgoingSchema]);

    const handleSaveExternalNumber = (lines: { line_id: string, priority: number }[], allLinesFromModal: Line[]) => {
        if (!editingNode) return;

        const newData = {
            ...editingNode.data,
            external_lines: lines,
            allLines: allLinesFromModal,
            hasExternalLines: lines.length > 0,
        };

        updateNodeData(editingNode.id, newData);
        handleCloseModals();
    };

    const nodesWithCallbacks = React.useMemo(() => {
        const sourceNodeIds = new Set(edges.map(edge => edge.source));

        return nodes.map(node => {
            let onAddClick: ((nodeId: string) => void) | undefined = handleAddNodeClick;

            // ИСПРАВЛЕНИЕ: Узел "Внешние линии" всегда тупиковый
            if (node.data.label === 'Внешние линии') {
                onAddClick = undefined;
            } else if (node.type === 'outgoing-call') {
                onAddClick = handleAddPatternCheckNode;
            } else if ([NodeType.WorkSchedule, NodeType.PatternCheck].includes(node.type as NodeType)) {
                onAddClick = undefined;
            }
            
            const singleOutputNodes: string[] = [NodeType.Start as string, NodeType.Greeting, NodeType.Dial, 'outgoing-call'];
            if ((singleOutputNodes.includes(node.type as string) || node.data.isSingleOutput) && sourceNodeIds.has(node.id)) {
                onAddClick = undefined;
            }

            return { ...node, data: { ...node.data, onAddClick, allLines: node.data.allLines || allLines } };
        });
    }, [nodes, edges, allLines]);

    return (
        <div className="schema-editor-container">
            <div className="schema-editor-header">
                <input
                    type="text"
                    value={schemaName}
                    onChange={(e) => setSchemaName(e.target.value)}
                    className="schema-name-input"
                    placeholder="Название схемы"
                    maxLength={35}
                />
                {!isOutgoingSchema && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '8px' }}>
                        <label style={{ fontSize: '0.95em', color: '#555' }}>Умная переадресация</label>
                        <input
                            type="checkbox"
                            checked={smartRedirect}
                            onChange={(e) => setSmartRedirect(e.target.checked)}
                        />
                    </div>
                )}
                {isOutgoingSchema ? (
                    <div style={{ marginTop: '-40px', paddingLeft: '700px' }}>
                        <h4 style={{ margin: '0', fontSize: '1em', color: '#555' }}>Используется менеджерами:</h4>
                        {outgoingCallNode?.data?.phones_details && outgoingCallNode.data.phones_details.length > 0 ? (
                            outgoingCallNode.data.phones_details.sort((a: any, b: any) => a.phone_number.localeCompare(b.phone_number, undefined, { numeric: true })).map((phone: any) => {
                                const nameParts = phone.full_name?.split(' ') || [];
                                const reversedName = nameParts.length > 1 ? [nameParts[1], nameParts[0]].join(' ') : phone.full_name;
                                return (
                                    <div key={phone.phone_number} style={{ fontSize: '0.9em', color: '#666' }}>
                                        {phone.phone_number} - {reversedName || 'Не назначен'}
                                    </div>
                                );
                            })
                        ) : (
                            <span style={{ fontSize: '0.9em', color: '#666' }}>Номера не выбраны</span>
                        )}
                    </div>
                ) : (
                    <div style={{ marginTop: '-40px', paddingLeft: '700px' }}>
                        <h4 style={{ margin: '0', fontSize: '1em', color: '#555' }}>Используются линиями:</h4>
                        {assignedLines.map(line => (
                            <div key={line.id} style={{ fontSize: '0.9em', color: '#666' }}>{line.display_name}</div>
                        ))}
                        {assignedLines.length === 0 && <span style={{ fontSize: '0.9em', color: '#666' }}>Нет привязанных линий</span>}
                    </div>
                )}
            </div>
            <div className="react-flow-wrapper" ref={reactFlowWrapper}>
                <ReactFlow
                    nodes={nodesWithCallbacks}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onConnect={onConnect}
                    onNodeClick={handleNodeClick}
                    nodeTypes={nodeTypes}
                    onInit={setReactFlowInstance}
                    fitView
                    className="react-flow-canvas"
                >
                </ReactFlow>
            </div>
            <div className="schema-editor-footer">
                <div className="footer-buttons-left">
                    <button 
                        onClick={handleDeleteClick} 
                        className="delete-button" 
                        disabled={isLoading}
                    >
                        Удалить схему
                    </button>
                </div>
                <div className="footer-buttons-right">
                    <button onClick={onCancel} className="cancel-button">Отмена</button>
                    <button onClick={handleSave} className="save-button" disabled={isLoading}>
                        {isLoading ? 'Сохранение...' : 'Сохранить'}
                    </button>
                </div>
            </div>
            {isLinesModalOpen && (
                <IncomingCallModal
                    enterpriseId={enterpriseId}
                    schemaName={schemaName}
                    initialSelectedLines={selectedLines}
                    onClose={() => setIsLinesModalOpen(false)}
                    onConfirm={handleLinesUpdate}
                />
            )}
            {isNodeActionModalOpen && sourceNodeForAction && (
                <NodeActionModal
                    sourceNodeType={sourceNodeForAction.type}
                    nodes={nodes}
                    onClose={() => setIsNodeActionModalOpen(false)}
                    onDialClick={() => handleOpenModalFor(NodeType.Dial)}
                    onGreetingClick={() => handleOpenModalFor(NodeType.Greeting)}
                    onWorkScheduleClick={() => handleOpenModalFor(NodeType.WorkSchedule)}
                />
            )}
            {isDialModalOpen && (
                <DialModal
                    enterpriseId={enterpriseId}
                    managers={dialManagers}
                    onClose={newNodeId ? handleCancelNodeCreation : handleCloseModals}
                    onConfirm={handleConfirmDial}
                    onAddManager={handleOpenAddManagerModal}
                    onRemoveManager={handleRemoveManager}
                    initialData={editingNode?.data}
                    onDelete={handleDeleteNode}
                />
            )}
            {isAddManagerModalOpen && (
                <AddManagerModal
                    enterpriseId={enterpriseId}
                    onClose={() => setIsAddManagerModalOpen(false)}
                    onAdd={handleAddManagers}
                    addedPhones={new Set(dialManagers.map(m => m.phone))}
                />
            )}
            {isGreetingModalOpen && (
                <GreetingModal
                    enterpriseId={enterpriseId}
                    onClose={newNodeId ? handleCancelNodeCreation : handleCloseModals}
                    onConfirm={handleConfirmGreeting}
                    initialData={editingNode?.data}
                    onDelete={handleDeleteNode}
                />
            )}
            {isWorkScheduleModalOpen && (
                 <WorkScheduleModal 
                    onClose={newNodeId ? handleCancelNodeCreation : handleCloseModals}
                    onConfirm={handleConfirmWorkSchedule}
                    initialData={editingNode?.data}
                    onDelete={handleDeleteNode}
                />
            )}
            {isOutgoingCallModalOpen && editingNode && (
                <OutgoingCallModal
                    isOpen={isOutgoingCallModalOpen}
                    onClose={handleCloseModals}
                    onConfirm={handleOutgoingNodeConfirm}
                    node={editingNode}
                    enterpriseId={enterpriseId}
                />
            )}
            {isPatternCheckModalOpen && (
                <PatternCheckModal
                    isOpen={isPatternCheckModalOpen}
                    onClose={handleCloseModals}
                    onSave={handlePatternCheckConfirm}
                    initialPatterns={editingNode?.data.patterns || []}
                />
            )}
            {isExternalNumberModalOpen && editingNode && (
                <ExternalNumberModal
                    isOpen={isExternalNumberModalOpen}
                    onClose={handleCloseModals}
                    onDelete={handleDeleteNode}
                    onConfirm={handleSaveExternalNumber}
                    enterpriseId={enterpriseId}
                    initialData={editingNode.data.external_lines || []}
                />
            )}
        </div>
    );
};

const SchemaEditorWrapper: React.FC<SchemaEditorWithProviderProps> = (props) => {
    return (
        <ReactFlowProvider>
            <SchemaEditor {...props} />
        </ReactFlowProvider>
    );
};

export default SchemaEditorWrapper;