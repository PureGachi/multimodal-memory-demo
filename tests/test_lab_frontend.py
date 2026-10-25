"""Exercise the actual inline JS with a minimal DOM; not a screenshot test."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import pytest


@pytest.mark.parametrize("kind", ["legacy_ocr", "text", "gallery_images", "rrf", "invalid"])
def test_report_dispatch_does_not_assume_metadata(kind):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute frontend regression tests")
    html = (Path(__file__).resolve().parents[1] / "src/memory_app/lab/index.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    report = {"samples": [{"name": "legacy", "query": "M42", "image": "photo.png",
                           "unit_id": "u1", "visible_text": "M42", "returned_ids": ["u1"]}]}
    if kind == "text":
        report = {"metadata": {"scenario": "old", "tested_questions": 0, "total_questions": 0},
                  "summary": {}, "details": [], "index_seconds": 0, "by_point": {}}
    elif kind in ("gallery_images", "rrf"):
        metric = {"hit_at_k": 1, "recall_at_k": 1, "first_evidence_rank": 1}
        report = {"metadata": {"scenario": "scene", "raw_images_used": True, "tested_questions": 1, "total_questions": 1},
                  "summary": {}, "image_summary": {}, "indexed_images": 1, "details": [
            {"number": 1, "point": "VS", "question": "M42", "clues": ["D1:1"], "answer": "D1:IMG_001",
             "query_images": [{"image": "assets/query.png"}], "query_image_status": "analyzed", "retrieval_query": "M42 derived query",
             "expected_image_basis": "answer_mentions", "expected_images": [{"image_id": "D1:IMG_001", "image": "assets/one.png"}],
             "gold_evidence": [{"round": "D1:1", "messages": [{"role": "assistant", "content": "photo", "images": ["assets/one.png"]}]}],
             "channels": {"lexical": {"metrics": metric, "image_metrics": metric, "latency_ms": 1,
                "returned": [{"rank": 1, "round": "D1:1", "role": "assistant", "score": 1,
                    "memory_id": "u1", "content": "photo", "images": ["assets/one.png"], "image_ids": ["D1:IMG_001"]}]}}}],
                  "index_seconds": 0, "by_point": {}}
    if kind == "rrf":
        report["metadata"]["experiment"] = "offline-rrf-weights-v1"
        report["weight_changes"] = {"RRF 0.25:1": [{"question": 1}]}
        channel = report["details"][0]["channels"]["lexical"]
        report["details"][0]["channels"] = {name: channel for name in ("lexical", "dense", "fusion", "RRF 0.25:1", "RRF 0.5:1")}
    elif kind == "invalid":
        report = {}
    harness = r"""
const vm=require('node:vm');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.hidden=false;}
 append(...nodes){this.children.push(...nodes);if(this.tag==='select'&&this.children.length===1)this.value=nodes[0].value;}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
}
const nodes=new Map();
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,new Element(['scenario','reports','questions','filter'].includes(id)?'select':'div'));return nodes.get(id);},createElement(tag){return new Element(tag);}};
const context={document,console,setInterval(){},fetch:async(path)=>({ok:true,json:async()=>path==='/api/scenarios'?[]:path==='/api/reports'?[{id:'test.json',scenario:'test',questions:1,top_k:3,channels:[]}]:path==='/api/job'?null:REPORT})};
vm.createContext(context);vm.runInContext(SCRIPT,context);
(async()=>{for(let n=0;n<10;n++)await new Promise(setImmediate);
 const message=document.getElementById('message').textContent;
 if(KIND==='invalid'){
  if(!message.includes('报告格式不受支持')||message.includes('undefined'))throw Error('Missing friendly error: '+message);
 }else{
  if(message.includes('Cannot read'))throw Error(message);
  const visible=KIND==='legacy_ocr'?'image-viewer':'viewer';
  if(document.getElementById(visible).hidden)throw Error('Wrong viewer');
  if(KIND==='legacy_ocr'&&document.getElementById('image-results').children.length!==1)throw Error('OCR cards not rendered');
  if(KIND==='gallery_images'){
   const images=node=>[...(node.tag==='img'?[node]:[]),...node.children.flatMap(images)];
   if(images(document.getElementById('expected-images')).length!==1)throw Error('Expected reference image missing');
   if(images(document.getElementById('question')).length!==1)throw Error('Query image missing');
   if(images(document.getElementById('channels')).length!==1)throw Error('Actual returned image missing');
  }
  if(KIND==='rrf'&&document.getElementById('channels').children.length!==5)throw Error('Weighted channels missing');
 }
 console.log('PASS '+KIND);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    harness = harness.replace("REPORT", json.dumps(report)).replace("SCRIPT", json.dumps(script)).replace("KIND", json.dumps(kind))
    result = subprocess.run([node], input=harness, text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
