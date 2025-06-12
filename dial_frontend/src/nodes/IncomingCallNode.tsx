import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import './IncomingCallNode.css';

interface IncomingCallNodeProps extends NodeProps {
    data: {
        label: string;
        onAddClick: () => void;
    };
}

const IncomingCallNode: React.FC<IncomingCallNodeProps> = ({ data }) => {
  return (
    <div className="incoming-call-node">
      <div className="node-content">
        {data.label}
      </div>
      <Handle type="source" position={Position.Bottom} id="a" />
      <div className="add-button-container">
        <button className="add-button" onClick={(e) => { e.stopPropagation(); data.onAddClick(); }}>+</button>
      </div>
    </div>
  );
};

export default memo(IncomingCallNode); 