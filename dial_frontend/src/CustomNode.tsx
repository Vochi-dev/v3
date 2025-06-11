import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

import './CustomNode.css';

// Using `any` for now to avoid typing issues and focus on functionality.
// We can add proper types later.
const CustomNode = memo(({ id, data }: { id: string; data: any }) => {
  return (
    <div className="custom-node-wrapper">
      <Handle type="target" position={Position.Top} style={{ background: 'transparent', border: 0 }} />
      <div className="custom-node">
        <div className="node-icon">
          <svg viewBox="0 0 24 24" strokeWidth="1.5" fill="none">
              <rect x="7" y="3" width="10" height="18" rx="2" ry="2" stroke="currentColor"/>
              <path d="M10 8 C 10 7, 14 7, 14 8 C 14 11, 9 13, 9 13 H 15 C 15 13, 10 11, 10 8 Z" fill="currentColor" stroke="none"/>
              <circle cx="12" cy="15" r="1" fill="currentColor"/>
              <path d="M5 9 a 4 4 0 0 1 0 6" stroke="currentColor" strokeLinecap="round"/>
              <path d="M19 9 a 4 4 0 0 0 0 6" stroke="currentColor" strokeLinecap="round"/>
          </svg>
        </div>
        <div className="node-label">{data.label}</div>
      </div>
      <button className="add-button" onClick={() => data.onAddClick(id)}>
          +
      </button>
      <Handle type="source" position={Position.Bottom} style={{ background: 'transparent', border: 0 }} />
    </div>
  );
});

export default CustomNode; 