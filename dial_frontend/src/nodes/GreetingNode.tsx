import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import './GreetingNode.css';

interface GreetingFile {
    id: number;
    name: string;
}

interface GreetingNodeData {
    label: string;
    greetingFile?: GreetingFile;
    onAddClick?: (nodeId: string, nodeType: string) => void;
}

const GreetingNode: React.FC<NodeProps<GreetingNodeData>> = ({ id, type, data }) => {
    const greetingName = data.greetingFile?.name || null;

    return (
        <div className="greeting-node">
            <Handle type="target" position={Position.Top} className="react-flow__handle" />
            <div className="node-content">
                <div className="node-label">{data.label}</div>
                {greetingName && <div className="greeting-name">{greetingName}</div>}
            </div>
            <Handle type="source" position={Position.Bottom} className="react-flow__handle" />
            {data.onAddClick && (
                <div className="add-button-container">
                    <button className="add-button" onClick={(e) => { e.stopPropagation(); data.onAddClick?.(id, type); }}>+</button>
                </div>
            )}
        </div>
    );
};

export default memo(GreetingNode); 