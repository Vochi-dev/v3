import { memo, useState } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import './CustomNode.css';
import AddNodeModal from './AddNodeModal';

const CustomNode = ({ data }: NodeProps) => {
    const [isAddNodeModalOpen, setAddNodeModalOpen] = useState(false);

    const openModal = (event: React.MouseEvent) => {
        event.stopPropagation();
        setAddNodeModalOpen(true);
    };

    return (
        <div className="custom-node">
            <Handle type="target" position={Position.Top} className="react-flow__handle" />

            <span className="node-icon">📞</span>
            <div>{data.label}</div>

            <button className="plus-button" onClick={openModal}>+</button>

            <Handle type="source" position={Position.Bottom} className="react-flow__handle" />

            {isAddNodeModalOpen && (
                <AddNodeModal onClose={() => setAddNodeModalOpen(false)} />
            )}
        </div>
    );
};

export default memo(CustomNode); 