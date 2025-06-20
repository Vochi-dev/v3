import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import './IncomingCallNode.css'; // Используем те же стили для единообразия

interface OutgoingCallNodeProps extends NodeProps {
    data: {
        label: string;
        onAddClick?: (nodeId: string) => void;
    };
}

const OutgoingCallNode: React.FC<OutgoingCallNodeProps> = ({ id, data }) => {
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
      {data.onAddClick && (
        <div className="add-button-container">
          <button className="add-button" onClick={(e) => { e.stopPropagation(); data.onAddClick && data.onAddClick(id); }}>+</button>
        </div>
      )}
    </div>
  );
};

export default memo(OutgoingCallNode); 