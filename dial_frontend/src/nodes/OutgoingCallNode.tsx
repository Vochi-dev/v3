import React from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import './IncomingCallNode.css'; // Используем те же стили для единообразия

const OutgoingCallNode: React.FC<NodeProps> = ({ data }) => {
  return (
    <div className="incoming-call-node">
      <div className="node-content">
        {data.label || 'Исходящий звонок'}
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        id="a"
        className="react-flow__handle"
      />
    </div>
  );
};

export default OutgoingCallNode; 