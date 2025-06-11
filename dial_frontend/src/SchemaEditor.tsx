import React, { useState, useEffect, useCallback, useRef } from 'react';
// Value imports from reactflow
import ReactFlow, {
  Controls,
  Background,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
} from 'reactflow';
// Type imports from reactflow
import type {
    Node,
    Edge,
    Connection,
    NodeChange,
    EdgeChange,
    NodeTypes,
    NodeMouseHandler
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useParams, useNavigate } from 'react-router-dom';

import CustomNode from './CustomNode';
import NodePickerModal from './NodePickerModal';
import { nodeDefinitions } from './node-definitions';
import IncomingCallModal from './IncomingCallModal';

const nodeTypes: NodeTypes = {
  custom: CustomNode,
};

const initialNodes: Node[] = [
  {
    id: '1',
    type: 'custom',
    data: { label: 'Поступил новый звонок' },
    position: { x: 250, y: 5 },
    draggable: false,
    deletable: false,
  },
];

const SchemaEditor: React.FC = () => {
  const { schemaId, enterpriseId } = useParams<{ schemaId: string; enterpriseId: string }>();
  const navigate = useNavigate();

  const [nodes, setNodes] = useState<Node[]>(initialNodes);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [schemaName, setSchemaName] = useState('');
  
  const [isPickerModalOpen, setPickerModalOpen] = useState(false);
  const [isIncomingCallModalOpen, setIncomingCallModalOpen] = useState(false);
  
  const [sourceNode, setSourceNode] = useState<Node | null>(null);
  const reactFlowWrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (schemaId && enterpriseId) {
      fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}`)
        .then((res) => res.json())
        .then((data) => {
          setNodes(data.nodes || initialNodes);
          setEdges(data.edges || []);
          setSchemaName(data.name || '');
        })
        .catch(error => console.error("Failed to fetch schema:", error));
    }
  }, [schemaId, enterpriseId]);

  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);

  const onConnect = useCallback((params: Connection) => setEdges((eds) => addEdge({ ...params, type: 'step' }, eds)), []);

  const handleSaveSchema = () => {
    if (!schemaId || !enterpriseId) return;
    const schemaData = {
      name: schemaName,
      nodes: nodes,
      edges: edges,
    };
    fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(schemaData),
    })
      .then(res => {
        if (res.ok) {
          alert('Схема сохранена!');
        } else {
          throw new Error('Failed to save schema');
        }
      })
      .catch(error => console.error("Failed to save schema:", error));
  };

  const handleAddNodeClick = (node: Node) => {
    setSourceNode(node);
    setPickerModalOpen(true);
  };

  const onNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    if (node.id === '1') {
      setIncomingCallModalOpen(true);
    } else {
      handleAddNodeClick(node);
    }
  }, []);

  const handleSelectNodeType = (type: string) => {
    if (!sourceNode) return;

    const definition = (nodeDefinitions as any)[type];
    if (!definition || typeof definition.label !== 'string') {
        console.error(`Node definition for type "${type}" is invalid or missing a label.`);
        return;
    }

    const newNodeId = (nodes.length + 1).toString();
    
    const newNode: Node = {
      id: newNodeId,
      type: 'custom',
      data: { label: definition.label },
      position: {
        x: sourceNode.position.x,
        y: sourceNode.position.y + 120, 
      },
      draggable: true,
      deletable: true,
    };
    const newEdge: Edge = {
      id: `e${sourceNode.id}-${newNodeId}`,
      source: sourceNode.id,
      target: newNodeId,
      type: 'step',
    };
    setNodes((nds) => [...nds, newNode]);
    setEdges((eds) => [...eds, newEdge]);
    setPickerModalOpen(false);
  };

  return (
    <div style={{ height: '100vh', width: '100%' }} ref={reactFlowWrapper}>
      <div style={{ display: 'flex', alignItems: 'center', padding: '10px', gap: '10px' }}>
        <button onClick={() => navigate(`/dial/enterprise/${enterpriseId}`)}>Назад к списку</button>
        <input
          type="text"
          value={schemaName}
          onChange={(e) => setSchemaName(e.target.value)}
          placeholder="Название схемы"
          style={{ flexGrow: 1, padding: '8px' }}
        />
        <button onClick={handleSaveSchema}>Сохранить схему</button>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick} 
        nodeTypes={nodeTypes}
        fitView
      >
        <Controls />
        <Background />
      </ReactFlow>
      <NodePickerModal
        isOpen={isPickerModalOpen}
        onClose={() => setPickerModalOpen(false)}
        onSelectNodeType={handleSelectNodeType}
      />
      {isIncomingCallModalOpen && enterpriseId && schemaId && (
          <IncomingCallModal
            isOpen={isIncomingCallModalOpen}
            onClose={() => setIncomingCallModalOpen(false)}
            enterpriseId={enterpriseId}
            schemaId={schemaId}
            schemaName={schemaName}
          />
      )}
    </div>
  );
};

export default SchemaEditor;