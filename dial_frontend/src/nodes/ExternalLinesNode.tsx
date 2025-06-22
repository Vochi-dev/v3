import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Line } from '../types';
import './ExternalLinesNode.css';

export interface ExternalLinesNodeData {
    label: string;
    allLines?: Line[];
    external_lines?: { line_id: string; priority: number }[];
}

const ExternalLinesNode: React.FC<NodeProps<ExternalLinesNodeData>> = ({ data }) => {
    const linesMap = new Map(data.allLines?.map(line => [line.id, line.display_name]));
    
    const sortedExternalLines = data.external_lines
        ? [...data.external_lines].sort((a, b) => a.priority - b.priority)
        : [];

    return (
        <div className="external-lines-node">
            <Handle type="target" position={Position.Top} isConnectable={true} />
            <div className="node-header">{data.label}</div>
            {sortedExternalLines.length > 0 && (
                <ul className="line-list">
                    {sortedExternalLines.map(({ line_id }) => {
                        const lineName = linesMap.get(line_id) || `(ID: ${line_id})`;
                        return (
                            <li key={line_id} className="line-item">
                                {lineName}
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
};

export default memo(ExternalLinesNode); 