import React from 'react';
import { Handle, Position } from 'reactflow';
import './CustomNode.css';

const CustomNode = ({ data }: { data: { label: string } }) => {
    return (
        <div className="custom-node">
            <Handle type="target" position={Position.Top} />
            <div className="node-content">
                <div className="icon">📞</div>
                <div>{data.label}</div>
            </div>
            <Handle type="source" position={Position.Bottom} />
        </div>
    );
};

export default React.memo(CustomNode); 