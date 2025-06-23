import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { ManagerInfo } from '../types';
import './DialNode.css';

interface HoldMusicInfo {
    type: 'default' | 'none' | 'custom';
    id?: number;
    name?: string;
}

interface DialNodeData {
    label: string;
    managers?: ManagerInfo[];
    waitingRings?: number;
    holdMusic?: HoldMusicInfo;
    onAddClick?: (nodeId: string, nodeType: string) => void;
}

const formatName = (fullName: string | undefined | null): string => {
    if (!fullName) {
        return 'Не назначен';
    }
    // Разделяем имя по пробелу, переворачиваем и соединяем обратно
    return fullName.split(' ').reverse().join(' ');
};

const DialNode: React.FC<NodeProps<DialNodeData>> = ({ id, type, data }) => {
    const hasManagers = data.managers && data.managers.length > 0;
    const nodeLabel = data.waitingRings ? `${data.label} (${data.waitingRings}г)` : data.label;
    const musicName = data.holdMusic?.type === 'custom' && data.holdMusic.name 
        ? data.holdMusic.name 
        : null;

    return (
        <div className="dial-node">
            <Handle type="target" position={Position.Top} className="react-flow__handle" />
            <div className="node-content">
                <div className="node-label">{nodeLabel}</div>
                {musicName && <div className="music-name">{musicName}</div>}
                {hasManagers && (
                    <div className="managers-list">
                        {data.managers?.map((manager, index) => (
                            <div key={index} className="manager-item">
                                {manager.phone} - {formatName(manager.name)}
                            </div>
                        ))}
                    </div>
                )}
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

export default memo(DialNode); 