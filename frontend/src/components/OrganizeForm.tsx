import { ReactNode, useState } from "react";
import { OrganizeOptions, Tier } from "../api";
import { TIER_INFO } from "../format";
import FolderPicker from "./FolderPicker";

const TIERS: Tier[] = ["exact", "duplicate", "near", "similar", "text"];

export function Choice<T extends string>({
  value,
  onChange,
  options,
  name,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { v: T; label: string; help: string }[];
  name: string;
}) {
  return (
    <div className="choices" role="radiogroup">
      {options.map((o) => (
        <label key={o.v} className={`choice ${value === o.v ? "on" : ""}`}>
          <input type="radio" name={name} checked={value === o.v} onChange={() => onChange(o.v)} />
          <span>
            <strong>{o.label}</strong>
            <span className="sub" style={{ display: "block", fontSize: 12 }}>{o.help}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

export function Section({ n, title, children }: { n?: number; title: string; children: ReactNode }) {
  return (
    <div className="card">
      <h2>{n !== undefined && <span className="step">{n}</span>}{title}</h2>
      {children}
    </div>
  );
}

/** A path field with a "Choose folder" button backed by the server-side folder picker. */
export function FolderField({
  value,
  onChange,
  placeholder,
  title,
  note,
}: {
  value: string;
  onChange: (p: string) => void;
  placeholder: string;
  title: string;
  note: string;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <>
      <div className="row">
        <input className="grow mono" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        <button type="button" onClick={() => setPicking(true)}>Choose folder</button>
      </div>
      {picking && (
        <FolderPicker
          title={title}
          note={note}
          onClose={() => setPicking(false)}
          onPick={(p) => {
            onChange(p);
            setPicking(false);
          }}
        />
      )}
    </>
  );
}

const EXAMPLE_NAMES: Record<OrganizeOptions["pattern"], string> = {
  date: "2019-08-12_15-30-12.jpg",
  date_name: "2019-08-12_15-30-12_IMG_1234.jpg",
  group: "g0003_01_IMG_1234.jpg  (01 = best copy)",
};

/** Destination, layout and naming options shared by Organize and Auto Process. */
export function OrganizeFields({
  o,
  set,
  numbered = true,
  scope,
}: {
  o: OrganizeOptions;
  set: <K extends keyof OrganizeOptions>(k: K, v: OrganizeOptions[K]) => void;
  numbered?: boolean;
  scope?: ReactNode;
}) {
  let n = 0;
  const next = () => (numbered ? ++n : undefined);
  const needsTiers = o.layout === "groups" || o.scope === "grouped" || o.rename === "duplicates" || o.pattern === "group";
  return (
    <>
      <Section n={next()} title="Destination">
        <FolderField
          value={o.dest}
          onChange={(p) => set("dest", p)}
          placeholder="Choose where the organized copy will be created…"
          title="Choose the destination"
          note="A new sub-folder will be created here."
        />
        <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>
          A new folder named “Organized &lt;date time&gt;” is created inside it, so nothing there is ever overwritten. It must be outside your scanned folders.
        </p>
      </Section>
      {scope && <Section n={next()} title="Which photos">{scope}</Section>}
      <Section n={next()} title="Folder layout">
        <Choice
          name="layout"
          value={o.layout}
          onChange={(v) => set("layout", v)}
          options={[
            { v: "groups", label: "One sub-folder per group", help: "g0001_<name>_(5)/ … for each group; photos with no match go to _unique/." },
            { v: "flat", label: "A single folder", help: "Every photo side by side in one folder." },
          ]}
        />
        {needsTiers && (
          <div style={{ marginTop: 12 }}>
            <h3>Groups are made of</h3>
            <div className="row">
              {TIERS.map((t) => (
                <label key={t} className={`badge ${o.tiers.includes(t) ? "accent" : ""}`} style={{ cursor: "pointer", padding: "5px 10px", fontSize: 12 }}>
                  <input
                    type="checkbox"
                    style={{ margin: "0 4px 0 0" }}
                    checked={o.tiers.includes(t)}
                    onChange={(e) => set("tiers", e.target.checked ? [...o.tiers, t] : o.tiers.filter((x) => x !== t))}
                  />
                  {TIER_INFO[t].label}
                </label>
              ))}
            </div>
            <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>Overlapping groups are merged, so each photo is copied once.</p>
          </div>
        )}
      </Section>
      <Section n={next()} title="File names">
        <Choice
          name="rename"
          value={o.rename}
          onChange={(v) => set("rename", v)}
          options={[
            { v: "keep", label: "Keep original names", help: "Only clashes are renamed: photo.jpg, photo_2.jpg, photo_3.jpg…" },
            { v: "duplicates", label: "Rename photos in groups", help: "Duplicates get a clear, unique name; other photos keep theirs." },
            { v: "all", label: "Rename all photos", help: "Every photo gets a name from the pattern below." },
          ]}
        />
        {o.rename !== "keep" && (
          <div className="row" style={{ marginTop: 10 }}>
            <span style={{ fontSize: 13 }}>Pattern</span>
            <select value={o.pattern} onChange={(e) => set("pattern", e.target.value as OrganizeOptions["pattern"])}>
              <option value="date_name">Date + original name</option>
              <option value="date">Date only</option>
              <option value="group">Group number + original name</option>
            </select>
            <span className="mono sub" style={{ fontSize: 12 }}>{EXAMPLE_NAMES[o.pattern]}</span>
          </div>
        )}
        <p className="sub" style={{ fontSize: 12, marginTop: 8 }}>
          Date = date taken (EXIF or file name), otherwise the last-modified date. Names never collide (checked case-insensitively, safe for Windows/USB drives).
        </p>
      </Section>
    </>
  );
}

export const DEFAULT_ORGANIZE: OrganizeOptions = {
  dest: "",
  scope: "not_discarded",
  layout: "groups",
  tiers: ["exact", "duplicate", "near"],
  one_per_exact: false,
  rename: "duplicates",
  pattern: "date_name",
};
