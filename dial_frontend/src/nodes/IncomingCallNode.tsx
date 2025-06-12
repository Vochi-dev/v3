import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import './IncomingCallNode.css';

const IncomingCallNode: React.FC<NodeProps> = ({ data }) => {
  return (
    <div className="incoming-call-node">
      <div className="node-content">
        {data.label}
      </div>
      <Handle type="source" position={Position.Bottom} id="a" />
      <div className="add-button-container">
        <button className="add-button">+</button>
      </div>
    </div>
  );
};

export default memo(IncomingCallNode); 