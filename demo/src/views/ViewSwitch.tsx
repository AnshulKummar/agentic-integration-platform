import { motion } from 'framer-motion'
import type { View } from '../scenario/types'
import type { ViewProps } from './shared'
import { RoadmapView } from './RoadmapView'
import { GraphView } from './GraphView'
import { WorkspaceView } from './WorkspaceView'
import { InboxView } from './InboxView'
import { ReviewView } from './ReviewView'
import { PipelineView } from './PipelineView'
import { LearningView } from './LearningView'

function renderView(view: View, props: ViewProps) {
  switch (view) {
    case 'roadmap':
      return <RoadmapView {...props} />
    case 'graph':
      return <GraphView {...props} />
    case 'workspace':
      return <WorkspaceView {...props} />
    case 'inbox':
      return <InboxView {...props} />
    case 'review':
      return <ReviewView {...props} />
    case 'pipeline':
      return <PipelineView {...props} />
    case 'learning':
      return <LearningView {...props} />
  }
}

/** Fades the view in when it changes; steps within the same view update in place. */
export function ViewSwitch(props: ViewProps) {
  const view = props.state.step.view
  return (
    <motion.div
      key={view}
      initial={props.reducedMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="h-full min-h-0"
      data-testid={`view-${view}`}
    >
      {renderView(view, props)}
    </motion.div>
  )
}
