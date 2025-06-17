import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import './IncomingCallNode.css'; // Re-use the existing styles

interface GenericNodeData {
    label: string;
    onAddClick?: (nodeId: string, nodeType: string) => void;
}

const GenericNode: React.FC<NodeProps<GenericNodeData>> = ({ id, type, data }) => {
    return (
        <div className="incoming-call-node">
            <Handle type="target" position={Position.Top} />
            <div className="node-content">
                {data.label}
            </div>
            <Handle type="source" position={Position.Bottom} />
            {data.onAddClick && (
                <div className="add-button-container">
                    <button className="add-button" onClick={(e) => { e.stopPropagation(); data.onAddClick && data.onAddClick(id, type); }}>+</button>
                </div>
            )}
        </div>
    );
};

export default memo(GenericNode); 