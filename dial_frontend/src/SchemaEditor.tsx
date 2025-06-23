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
    [NodeType.Greeting]: GenericNode,
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
            alert("Нельзя удалить схему, пока к ней привязаны линии. Сначала отвяжите их в узле 'Поступил новый звонок'.");
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
            schema_data: { nodes, edges, viewport }
        };

        try {
            const savedSchema = await onSave(schemaToSave);
            
            if (savedSchema && savedSchema.schema_id) {
                const res = await fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${savedSchema.schema_id}/assign_lines`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Array.from(selectedLines)),
                });

                if (!res.ok) {
                   throw new Error('Ошибка привязки линий');
                }
            }
            onCancel();
        } catch (error) {
            console.error("Failed to save schema or assign lines:", error);
            alert("Не удалось сохранить схему или привязать линии.");
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

        // 2. HACK: Особая логика для "Внешних линий"
        if (isOutgoingSchema && type === NodeType.Greeting && sourceNodeForAction.type === NodeType.Dial) {
            console.log("Обнаружен особый случай: 'Внешние линии'.");
            nodeData = {
                label: 'Внешние линии',
                external_lines: [],
                onAddClick: undefined // Сразу делаем тупиковым
            };
        }

        // --- НАЧАЛО: Логика наследования для узла "Звонок на список" ---
        if (type === NodeType.Dial && parentNode.type === NodeType.Dial) {
            const parentData = parentNode.data;
            if (parentData.managers) {
                initialManagers = parentData.managers.filter((m: ManagerInfo) => m.phone && m.phone.length <= 4);
            }
            if (parentData.holdMusic) {
                nodeData.holdMusic = parentData.holdMusic;
            }
        }
        // --- КОНЕЦ: Логика наследования ---

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
        const parentNodeFromAction = sourceNodeForAction ? sourceNodeForAction.node : null;
        const parentNode = editingNode || parentNodeFromAction;
        if (!parentNode) return;
    
        let nextNodes = [...nodes];
        let nextEdges = [...edges];
        let workScheduleNode: Node | undefined;
    
        if (editingNode) {
            workScheduleNode = editingNode;
            const updatedData = {
                label: workScheduleNode.data.label,
                periods: periods.map(p => ({...p, days: Array.from(p.days)})),
                onAddClick: undefined
            };
            nextNodes = nextNodes.map(n => n.id === editingNode.id ? { ...n, data: updatedData } : n);
        } else if (parentNodeFromAction) {
            const rule = getNodeRule(NodeType.WorkSchedule)!;
            const newNodeId = (Math.max(0, ...nextNodes.map(n => parseInt(n.id, 10))) + 1).toString();
            
            workScheduleNode = {
                id: newNodeId,
                type: NodeType.WorkSchedule,
                position: { x: parentNodeFromAction.position.x, y: parentNodeFromAction.position.y + 150 },
                data: { 
                    label: rule.name,
                    periods: periods.map(p => ({ ...p, days: Array.from(p.days) })),
                    onAddClick: undefined
                },
            };
            nextNodes.push(workScheduleNode);
    
            const newEdge: Edge = { id: `e${parentNodeFromAction.id}-${workScheduleNode.id}`, source: parentNodeFromAction.id, target: workScheduleNode.id };
            nextEdges.push(newEdge);
        }
    
        if (!workScheduleNode) return;
    
        const childEdges = nextEdges.filter(e => e.source === workScheduleNode!.id);
        const childNodeIds = new Set(childEdges.map(e => e.target));
        nextEdges = nextEdges.filter(e => e.source !== workScheduleNode!.id);
        let finalNodes = nextNodes.filter(n => !childNodeIds.has(n.id));
    
        let lastNodeId = Math.max(0, ...nodes.map(n => parseInt(n.id, 10)), ...finalNodes.map(n => parseInt(n.id, 10)));
        const parentPos = workScheduleNode.position;
        const horizontalSpacing = 280;
        const verticalSpacing = 120;
        const totalWidth = horizontalSpacing * (periods.length + 1);
        const startX = parentPos.x - totalWidth / 2 + horizontalSpacing / 2;
    
        periods.forEach((period, index) => {
            lastNodeId++;
            const newNodeId = lastNodeId.toString();
            const daysLabel = formatDays(period.days);
            const label = `${daysLabel} ${period.startTime}-${period.endTime}`;
            
            const newNode: Node = {
                id: newNodeId,
                type: NodeType.IVR,
                position: { x: startX + index * horizontalSpacing, y: parentPos.y + verticalSpacing },
                data: { label, onAddClick: handleAddNodeClick, isSingleOutput: true },
            };
            finalNodes.push(newNode);
            nextEdges.push({ id: `e${workScheduleNode!.id}-${newNodeId}`, source: workScheduleNode!.id, target: newNodeId });
        });
    
        lastNodeId++;
        const elseNodeId = lastNodeId.toString();
        const elseNode: Node = {
            id: elseNodeId,
            type: NodeType.IVR,
            position: { x: startX + periods.length * horizontalSpacing, y: parentPos.y + verticalSpacing },
            data: { label: 'Остальное время', onAddClick: handleAddNodeClick, isSingleOutput: true },
        };
        finalNodes.push(elseNode);
        nextEdges.push({ id: `e${workScheduleNode!.id}-${elseNodeId}`, source: workScheduleNode!.id, target: elseNodeId });
    
        setNodes(finalNodes);
        setEdges(nextEdges);
    
        setIsWorkScheduleModalOpen(false);
        setEditingNode(null);
        setSourceNodeForAction(null);
    };

    const handleConfirmGreeting = (greetingData: any) => {
        if (editingNode) {
            updateNodeData(editingNode.id, { greetingFile: greetingData.greetingFile });
        } else {
            createNewNode(NodeType.Greeting, { greetingFile: greetingData.greetingFile });
        }
        setIsGreetingModalOpen(false);
        setEditingNode(null);
    };

    const autosaveSchema = (updatedNodes?: Node[]) => {
        // Эта функция сохраняет схему в фоне, без закрытия редактора.
        const viewport = reactFlowInstance?.getViewport() || { x: 0, y: 0, zoom: 1 };
        const schemaToSave: Partial<Schema> = {
            ...schema,
            enterprise_id: enterpriseId,
            schema_name: schemaName,
            schema_data: { nodes: updatedNodes || nodes, edges, viewport }
        };

        onSave(schemaToSave).catch(error => {
            console.error("Autosave failed:", error);
            // Здесь можно добавить уведомление для пользователя, если необходимо
        });
    };

    const handlePatternCheckConfirm = (patterns: any[]) => {
        if (!sourceNodeForAction?.node && !editingNode) {
            console.error("No source or editing node defined for pattern check");
            return;
        }

        let lastNodeId = Math.max(0, ...nodes.map(n => parseInt(n.id, 10) || 0));

        const generateNewNodesAndEdges = (parentNode: Node, newPatterns: any[]) => {
            const newNodes: Node[] = [];
            const newEdges: Edge[] = [];
            
            const parentPosition = parentNode.position || { x: 0, y: 0 };
            const horizontalOffset = 200;
            const yOffset = 180;
            const totalWidth = (newPatterns.length - 1) * horizontalOffset;
            const startX = parentPosition.x - totalWidth / 2;

            newPatterns.forEach((pattern, index) => {
                lastNodeId++;
                const childNodeId = lastNodeId.toString();

                const childNode: Node = {
                    id: childNodeId,
                    type: NodeType.Greeting,
                    position: {
                        x: startX + (index * horizontalOffset),
                        y: parentPosition.y + yOffset
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
                    type: 'default',
                };
                newEdges.push(childEdge);
            });

            return { newNodes, newEdges };
        };

        // СЦЕНАРИЙ 2: РЕДАКТИРОВАНИЕ СУЩЕСТВУЮЩЕГО УЗЛА
        if (editingNode) {
            const existingChildEdges = edges.filter(e => e.source === editingNode.id);
            const existingChildNodeIds = new Set(existingChildEdges.map(e => e.target));

            const { newNodes, newEdges } = generateNewNodesAndEdges(editingNode, patterns);

            setNodes(nds => 
                nds.filter(n => !existingChildNodeIds.has(n.id)) // Удаляем старые дочерние узлы
                   .map(n => n.id === editingNode.id ? { ...n, data: { ...n.data, patterns } } : n) // Обновляем patterns у родителя
                   .concat(newNodes) // Добавляем новые дочерние узлы
            );
            setEdges(eds => 
                eds.filter(e => e.source !== editingNode.id) // Удаляем старые дочерние связи
                   .concat(newEdges) // Добавляем новые
            );
        }
        // СЦЕНАРИЙ 1: СОЗДАНИЕ НОВОГО УЗЛА "ПРОВЕРКА ПО ШАБЛОНУ" И ЕГО ВЕТОК
        else if (sourceNodeForAction?.node) {
            const sourceNode = sourceNodeForAction.node;
            const rule = getNodeRule(NodeType.PatternCheck)!;
            
            lastNodeId++;
            const patternCheckNodeId = lastNodeId.toString();
            
            const patternCheckNode: Node = {
                id: patternCheckNodeId,
                type: NodeType.PatternCheck,
                position: { x: sourceNode.position.x, y: sourceNode.position.y + 150 },
                data: { 
                    label: rule.name,
                    patterns: patterns,
                    onAddClick: undefined // У этого узла нет кнопки "+"
                },
            };

            const edgeToPatternCheck: Edge = {
                id: `e${sourceNode.id}-${patternCheckNodeId}`,
                source: sourceNode.id,
                target: patternCheckNodeId,
                type: 'smoothstep'
            };

            const { newNodes: childNodes, newEdges: childEdges } = generateNewNodesAndEdges(patternCheckNode, patterns);

            setNodes(nds => 
                nds.map(n => n.id === sourceNode.id ? { ...n, data: { ...n.data, onAddClick: undefined } } : n) // Убираем "+" у родителя
                   .concat(patternCheckNode)
                   .concat(childNodes)
            );
            setEdges(eds => [...eds, edgeToPatternCheck, ...childEdges]);
        }

        handleCloseModals();
    };

    const handleOutgoingNodeConfirm = (nodeId: string, data: any) => {
        // Обновляем состояние узлов локально
        const newNodes = nodes.map(node => {
            if (node.id === nodeId) {
                return { ...node, data: { ...node.data, ...data } };
            }
            return node;
        });
        setNodes(newNodes);
        
        // Сразу же сохраняем изменения на сервере
        autosaveSchema(newNodes);
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
                {isOutgoingSchema ? (
                    <div style={{ marginTop: '-40px', paddingLeft: '700px' }}>
                        <h4 style={{ margin: '0', fontSize: '1em', color: '#555' }}>Используются линии:</h4>
                        {outgoingCallNode?.data?.phones_details && outgoingCallNode.data.phones_details.length > 0 ? (
                            outgoingCallNode.data.phones_details.sort((a: any, b: any) => a.phone_number.localeCompare(b.phone_number, undefined, { numeric: true })).map((phone: any) => (
                                <div key={phone.phone_number} style={{ fontSize: '0.9em', color: '#666' }}>
                                    {phone.phone_number} - {phone.full_name || 'Не назначен'}
                                </div>
                            ))
                        ) : (
                            <span style={{ fontSize: '0.9em', color: '#666' }}>Номера не выбраны</span>
                        )}
                    </div>
                ) : (
                    <div style={{ marginTop: '-40px', paddingLeft: '700px' }}>
                        <h4 style={{ margin: '0', fontSize: '1em', color: '#555' }}>Используются линии:</h4>
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
                    onClose={handleCloseModals}
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
                    onClose={handleCloseModals}
                    onConfirm={handleConfirmGreeting}
                    initialData={editingNode?.data}
                    onDelete={handleDeleteNode}
                />
            )}
            {isWorkScheduleModalOpen && (
                 <WorkScheduleModal 
                    onClose={handleCloseModals}
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