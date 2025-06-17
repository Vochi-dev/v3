import React, { useState, useCallback, useEffect, useRef } from 'react';
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
import GenericNode from './nodes/GenericNode';
import { Schema, Line } from './types';
import IncomingCallModal from './IncomingCallModal';
import NodeActionModal from './NodeActionModal';
import DialModal from './DialModal';
import AddManagerModal from './AddManagerModal';
import GreetingModal from './GreetingModal';
import WorkScheduleModal, { SchedulePeriod } from './WorkScheduleModal';
import { NodeType, getNodeRule } from './nodeRules';

interface ManagerInfo {
    userId: number;
    name: string;
    phone: string;
}

interface SchemaEditorProps {
    enterpriseId: string;
    schema: Partial<Schema>;
    onSave: (schema: Partial<Schema>) => Promise<Schema>;
    onCancel: () => void;
    onDelete: (schemaId: string) => void;
}

const nodeTypes = {
    [NodeType.Start]: IncomingCallNode,
    [NodeType.Greeting]: GenericNode,
    [NodeType.Dial]: GenericNode,
    [NodeType.WorkSchedule]: GenericNode,
    [NodeType.IVR]: GenericNode, // For future use
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

const SchemaEditor: React.FC<SchemaEditorProps> = ({ enterpriseId, schema, onSave, onCancel, onDelete }) => {
    const [nodes, setNodes] = useState<Node[]>(schema.schema_data?.nodes || []);
    const [edges, setEdges] = useState<Edge[]>(schema.schema_data?.edges || []);
    const [schemaName, setSchemaName] = useState(schema.schema_name || 'Новая схема');
    const [isLinesModalOpen, setIsLinesModalOpen] = useState(false);
    const [isNodeActionModalOpen, setIsNodeActionModalOpen] = useState(false);
    const [isDialModalOpen, setIsDialModalOpen] = useState(false);
    const [isAddManagerModalOpen, setIsAddManagerModalOpen] = useState(false);
    const [isGreetingModalOpen, setIsGreetingModalOpen] = useState(false);
    const [isWorkScheduleModalOpen, setIsWorkScheduleModalOpen] = useState(false);
    const [dialManagers, setDialManagers] = useState<ManagerInfo[]>([]);
    const [editingNode, setEditingNode] = useState<Node | null>(null);
    
    const [sourceNodeForAction, setSourceNodeForAction] = useState<{node: Node, type: NodeType} | null>(null);

    const [selectedLines, setSelectedLines] = useState<Set<string>>(new Set());
    const [allLines, setAllLines] = useState<Line[]>([]);
    const [isLoading, setIsLoading] = useState(false);

    const reactFlowWrapper = useRef<HTMLDivElement>(null);
    const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null);

    useEffect(() => {
        if (schema.schema_id) {
            fetch(`/dial/api/enterprises/${enterpriseId}/lines`)
                .then(res => res.ok ? res.json() : Promise.reject(res))
                .then((data: Line[]) => {
                    setAllLines(data);
                    const normalizedSchemaName = schemaName.trim().toLowerCase();
                    const initiallySelected = new Set(
                        data.filter(line => line.in_schema && line.in_schema.trim().toLowerCase() === normalizedSchemaName).map(line => line.id)
                    );
                    setSelectedLines(initiallySelected);
                })
                .catch(err => console.error("Не удалось загрузить линии для схемы", err));
        }
    }, [schema.schema_id, schema.schema_name, enterpriseId, schemaName]);


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
            case NodeType.Start:
                setIsLinesModalOpen(true);
                break;
            case NodeType.Greeting:
                setIsGreetingModalOpen(true);
                break;
            case NodeType.Dial:
                setDialManagers(node.data.managers || []);
                setIsDialModalOpen(true);
                break;
            case NodeType.WorkSchedule:
                setIsWorkScheduleModalOpen(true);
                break;
            default:
                setEditingNode(null);
                break;
        }
    };

    const handleAddNodeClick = (nodeId: string) => {
        const sourceNode = nodes.find(n => n.id === nodeId);
        if (sourceNode) {
            setSourceNodeForAction({ node: sourceNode, type: sourceNode.type as NodeType });
            setIsNodeActionModalOpen(true);
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
        setIsNodeActionModalOpen(false);
        setEditingNode(null);
        if(type === NodeType.Dial) {
            setDialManagers([]);
            setIsDialModalOpen(true);
        }
        if(type === NodeType.Greeting) setIsGreetingModalOpen(true);
        if(type === NodeType.WorkSchedule) setIsWorkScheduleModalOpen(true);
    }
    
    const updateNodeData = (nodeId: string, data: any) => {
        setNodes(nds => nds.map(n => {
            if (n.id === nodeId) {
                return { ...n, data: { ...n.data, ...data } };
            }
            return n;
        }));
    };

    const handleConfirmWorkSchedule = (periods: SchedulePeriod[]) => {
        const parentNodeFromAction = sourceNodeForAction ? sourceNodeForAction.node : null;
        const parentNode = editingNode || parentNodeFromAction;
        if (!parentNode) return;
    
        let nextNodes = [...nodes];
        let nextEdges = [...edges];
        let workScheduleNode: Node | undefined;
    
        // Шаг 1: Определяем или создаем узел "График работы"
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
    
        // Шаг 2: Удаляем старые дочерние узлы и ребра
        const childEdges = nextEdges.filter(e => e.source === workScheduleNode!.id);
        const childNodeIds = new Set(childEdges.map(e => e.target));
        nextEdges = nextEdges.filter(e => e.source !== workScheduleNode!.id);
        // Мы не удаляем дочерние узлы из nextNodes, а просто перезаписываем их ниже
        let finalNodes = nextNodes.filter(n => !childNodeIds.has(n.id));
    
        // Шаг 3: Создаем новые дочерние узлы и ребра
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
                data: { label, onAddClick: handleAddNodeClick },
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
            data: { label: 'Остальное время', onAddClick: handleAddNodeClick },
        };
        finalNodes.push(elseNode);
        nextEdges.push({ id: `e${workScheduleNode!.id}-${elseNodeId}`, source: workScheduleNode!.id, target: elseNodeId });
    
        // Шаг 4: Атомарно обновляем состояние
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
        setEditingNode(null);
        setDialManagers([]);
    };

    const handleOpenAddManagerModal = () => {
        setIsAddManagerModalOpen(true);
    };

    const handleAddManagers = (selected: ManagerInfo[]) => {
        setDialManagers(prev => [...prev, ...selected]);
    };

    const handleRemoveManager = (indexToRemove: number) => {
        setDialManagers(prev => prev.filter((_, index) => index !== indexToRemove));
    };

    const handleLinesUpdate = async (newSelectedLines: Set<string>) => {
        setSelectedLines(newSelectedLines);
        setIsLinesModalOpen(false);

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
            } finally {
                setIsLoading(false);
            }
        }
    };

    const assignedLines = allLines.filter(line => selectedLines.has(line.id));

    const nodesWithCallbacks = React.useMemo(() => {
        return nodes.map(node => {
            if (node.type === NodeType.WorkSchedule) {
                return {
                    ...node,
                    data: {
                        ...node.data,
                        onAddClick: undefined,
                    },
                };
            }
            return {
                ...node,
                data: {
                    ...node.data,
                    onAddClick: handleAddNodeClick,
                },
            };
        });
    }, [nodes]);

    const handleDeleteNode = () => {
        if (!editingNode) return;

        if (editingNode.id === '1') {
            alert("Стартовый узел 'Входящий звонок' удалить нельзя.");
            return;
        }

        const idsToDelete = new Set<string>();
        const queue: string[] = [editingNode.id];
        idsToDelete.add(editingNode.id);

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
        setEdges(eds => eds.filter(e => !idsToDelete.has(e.source)));
        
        handleCloseModals();
    };

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
                <div style={{ marginTop: '-40px', paddingLeft: '700px' }}>
                    <h4 style={{ margin: '0', fontSize: '1em', color: '#555' }}>Используются линии:</h4>
                    {assignedLines.map(line => (
                        <div key={line.id} style={{ fontSize: '0.9em', color: '#666' }}>{line.display_name}</div>
                    ))}
                </div>
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
                    <button onClick={() => schema.schema_id && onDelete(schema.schema_id)} className="delete-button" disabled={isLoading || !schema.schema_id}>Удалить схему</button>
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
                    addedManagerIds={new Set(dialManagers.map(m => m.userId))}
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
        </div>
    );
};

// Обертка для доступа к хуку useReactFlow
const SchemaEditorWrapper: React.FC<SchemaEditorProps> = (props) => {
    return (
        <ReactFlowProvider>
            <SchemaEditor {...props} />
        </ReactFlowProvider>
    );
};

export default SchemaEditorWrapper; 