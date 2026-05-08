CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS photo_spots (
  id TEXT PRIMARY KEY,
  city TEXT NOT NULL,
  name TEXT NOT NULL,
  address TEXT,
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  geo_verified BOOLEAN DEFAULT FALSE,
  spot_type TEXT,
  suitable_styles JSONB DEFAULT '[]'::jsonb,
  visual_elements JSONB DEFAULT '[]'::jsonb,
  best_time_hint JSONB DEFAULT '[]'::jsonb,
  weather_preference JSONB DEFAULT '[]'::jsonb,
  ticket_required BOOLEAN DEFAULT FALSE,
  ticket_note TEXT,
  opening_hours JSONB,
  crowd_risk TEXT,
  phone_friendly BOOLEAN DEFAULT TRUE,
  base_photo_score DOUBLE PRECISION,
  shooting_tips JSONB DEFAULT '[]'::jsonb,
  source_type TEXT DEFAULT 'seed',
  source_urls JSONB DEFAULT '[]'::jsonb,
  raw JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_photo_spots_city ON photo_spots(city);
CREATE INDEX IF NOT EXISTS idx_photo_spots_styles ON photo_spots USING GIN(suitable_styles);
CREATE INDEX IF NOT EXISTS idx_photo_spots_elements ON photo_spots USING GIN(visual_elements);
ALTER TABLE IF EXISTS photo_spots ADD COLUMN IF NOT EXISTS geo_verified BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS travel_plans (
  id UUID PRIMARY KEY,
  title TEXT,
  destination TEXT,
  departure_city TEXT,
  date_range JSONB DEFAULT '[]'::jsonb,
  shooting_style JSONB DEFAULT '[]'::jsonb,
  visual_elements JSONB DEFAULT '[]'::jsonb,
  subject JSONB DEFAULT '[]'::jsonb,
  platform JSONB DEFAULT '[]'::jsonb,
  equipment JSONB DEFAULT '[]'::jsonb,
  budget INT,
  status TEXT DEFAULT 'draft',
  user_input TEXT NOT NULL,
  reference_images JSONB DEFAULT '[]'::jsonb,
  request_hash TEXT,
  parsed_goal JSONB DEFAULT '{}'::jsonb,
  visual_goal JSONB DEFAULT '{}'::jsonb,
  weather_context JSONB DEFAULT '{}'::jsonb,
  sunlight_context JSONB DEFAULT '{}'::jsonb,
  map_context JSONB DEFAULT '{}'::jsonb,
  reference_context JSONB DEFAULT '{}'::jsonb,
  discovery_context JSONB DEFAULT '{}'::jsonb,
  image_analysis JSONB DEFAULT '{}'::jsonb,
  repair_context JSONB DEFAULT '{}'::jsonb,
  task_plan JSONB DEFAULT '[]'::jsonb,
  agent_steps JSONB DEFAULT '[]'::jsonb,
  backup_plan JSONB DEFAULT '[]'::jsonb,
  final_markdown TEXT,
  plan_json JSONB DEFAULT '{}'::jsonb,
  warnings JSONB DEFAULT '[]'::jsonb,
  llm_used BOOLEAN DEFAULT FALSE,
  execution_state JSONB DEFAULT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_travel_plans_destination ON travel_plans(destination);
CREATE INDEX IF NOT EXISTS idx_travel_plans_status ON travel_plans(status);
CREATE INDEX IF NOT EXISTS idx_travel_plans_updated_at ON travel_plans(updated_at DESC);
ALTER TABLE IF EXISTS travel_plans ADD COLUMN IF NOT EXISTS execution_state JSONB DEFAULT NULL;
ALTER TABLE IF EXISTS travel_plans ADD COLUMN IF NOT EXISTS map_context JSONB DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS travel_plans ADD COLUMN IF NOT EXISTS reference_context JSONB DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS travel_plans ADD COLUMN IF NOT EXISTS discovery_context JSONB DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS travel_plans ADD COLUMN IF NOT EXISTS image_analysis JSONB DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS travel_plans ADD COLUMN IF NOT EXISTS repair_context JSONB DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS travel_plans ADD COLUMN IF NOT EXISTS reference_images JSONB DEFAULT '[]'::jsonb;
ALTER TABLE IF EXISTS travel_plans ADD COLUMN IF NOT EXISTS request_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_travel_plans_request_hash ON travel_plans(request_hash);

CREATE TABLE IF NOT EXISTS agent_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id UUID NOT NULL REFERENCES travel_plans(id) ON DELETE CASCADE,
  step_index INT NOT NULL,
  task_id TEXT,
  step_type TEXT,
  reasoning_summary TEXT,
  tool_name TEXT,
  tool_input JSONB DEFAULT '{}'::jsonb,
  tool_output JSONB DEFAULT '{}'::jsonb,
  observation JSONB DEFAULT '{}'::jsonb,
  duration_ms INT,
  success BOOLEAN,
  source TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(plan_id, step_index)
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_plan_id ON agent_steps(plan_id);
ALTER TABLE IF EXISTS agent_steps ADD COLUMN IF NOT EXISTS duration_ms INT;

CREATE TABLE IF NOT EXISTS spot_time_options (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id UUID NOT NULL REFERENCES travel_plans(id) ON DELETE CASCADE,
  option_id TEXT NOT NULL,
  spot_id TEXT REFERENCES photo_spots(id),
  spot_name TEXT NOT NULL,
  date TEXT,
  time_window TEXT,
  start_time TEXT,
  end_time TEXT,
  slot_type TEXT,
  light_label TEXT,
  shoot_goal TEXT,
  expected_visual JSONB DEFAULT '[]'::jsonb,
  style_fit DOUBLE PRECISION,
  visual_element_fit DOUBLE PRECISION,
  light_fit DOUBLE PRECISION,
  weather_fit DOUBLE PRECISION,
  transport_fit DOUBLE PRECISION,
  risk_score DOUBLE PRECISION,
  ticket_fit DOUBLE PRECISION,
  constraint_fit DOUBLE PRECISION,
  final_score DOUBLE PRECISION,
  risks JSONB DEFAULT '[]'::jsonb,
  recommended_shots JSONB DEFAULT '[]'::jsonb,
  data JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(plan_id, option_id)
);

CREATE INDEX IF NOT EXISTS idx_spot_time_options_plan_id ON spot_time_options(plan_id);
CREATE INDEX IF NOT EXISTS idx_spot_time_options_score ON spot_time_options(final_score DESC);

CREATE TABLE IF NOT EXISTS plan_route_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id UUID NOT NULL REFERENCES travel_plans(id) ON DELETE CASCADE,
  option_id TEXT,
  sequence INT NOT NULL,
  date TEXT,
  start_time TEXT,
  end_time TEXT,
  item_type TEXT DEFAULT 'shoot',
  spot_name TEXT,
  shoot_goal TEXT,
  route_note TEXT,
  guide JSONB DEFAULT '{}'::jsonb,
  completed BOOLEAN DEFAULT FALSE,
  skipped BOOLEAN DEFAULT FALSE,
  data JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_route_items_plan_id ON plan_route_items(plan_id);

CREATE TABLE IF NOT EXISTS plan_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id UUID NOT NULL REFERENCES travel_plans(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  reference_images JSONB DEFAULT '[]'::jsonb,
  tool_requests JSONB DEFAULT '[]'::jsonb,
  tool_results JSONB DEFAULT '[]'::jsonb,
  response JSONB DEFAULT '{}'::jsonb,
  warnings JSONB DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_messages_plan_id ON plan_messages(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_messages_created_at ON plan_messages(created_at DESC);
