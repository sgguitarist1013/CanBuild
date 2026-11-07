/* Explicit program assumptions; never an automatically approved unit yield. */
function addCondoMix(label='Condo unit type') {
  const row=document.createElement('div'); row.className='unit-mix-row';
  for(const [key,title,type,placeholder] of [
    ['label','Unit type','text','e.g. 2-bedroom'],['units','Condo quantity','number','0'],
    ['area_sqft','Average interior unit area (sq ft)','number','Enter unit area'],
    ['low_price_per_sqft','Low sale assumption ($/sq ft)','number','Enter pricing'],
    ['high_price_per_sqft','High sale assumption ($/sq ft)','number','Enter pricing']]) {
    const field=document.createElement('label'); field.textContent=title;
    const input=document.createElement('input'); input.type=type; input.dataset.field=key; input.placeholder=placeholder;
    if(type==='number'){input.min=key==='units'?'0':'0.01';input.step=key==='units'?'1':'0.01';}
    else {input.value=label;input.maxLength=80;}
    field.append(input);row.append(field);
  }
  $('condoUnitMix').append(row);
}

function renderCondoProgram(report) {
  const b=report.existing_building||{};
  const units=report.dwelling_unit_summary?.existing_reported_total??b.units;
  $('existingDwellingUnits').textContent=units!==null&&units!==undefined?fmt(units):'Unresolved';
  $('dwellingStatus').textContent=report.dwelling_unit_summary?.note||b.unit_count_note||'Existing dwelling count requires assessor and permit confirmation.';
  $('proposedDwellingUnits').textContent='Define unit mix';$('condoSellout').textContent='Needs pricing';
  $('unitTotalsNote').textContent='Proposed unit counts require a building program and local review.';
  $('condoUnitMix').replaceChildren();addCondoMix('1-bedroom');addCondoMix('2-bedroom');
  $('retainedUnits').value='0';$('restrictedUnits').value='0';$('restrictedUnitPrice').value='';
  $('condoScenarioStatus').textContent='';$('condoReferences').replaceChildren();
  $('condoCompStatus').textContent='Load condominium sales to review possible pricing references.';
  $('condoMinYear').value=String(new Date().getFullYear()-5);$('condoMinYear').max=String(new Date().getFullYear());
  $('applyCondoRange').disabled=true;
  $('loadCondoSales').disabled=!(report.parcel&&report.coordinates&&report.coverage?.municipality);
}

function nullableNumber(value){return value.trim()===''?null:Number(value);}

async function calculateCondoProgram() {
  if(!last)return;
  const report=last;
  const market_units=[...$('condoUnitMix').children].map(row=>{
    const fields={};for(const input of row.querySelectorAll('input'))fields[input.dataset.field]=input.dataset.field==='label'?input.value:input.dataset.field==='units'?Number(input.value||0):nullableNumber(input.value);
    return fields;
  });
  const body={existing_units_reported:report.dwelling_unit_summary?.existing_reported_total??report.existing_building?.units??null,
    retained_units_outside_sellout:Number($('retainedUnits').value||0),market_units,
    restricted_units:Number($('restrictedUnits').value||0),restricted_sale_price_per_unit:nullableNumber($('restrictedUnitPrice').value)};
  $('calculateCondo').disabled=true;$('condoScenarioStatus').textContent='Calculating program assumptions…';
  $('proposedDwellingUnits').textContent='Calculating';$('condoSellout').textContent='Needs pricing';
  try{
    const d=await jsonResponse('/api/condo-scenario',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(last!==report)return;
    report.condo_scenario=d;
    $('proposedDwellingUnits').textContent=d.total_proposed_dwelling_units===null?'Define unit mix':fmt(d.total_proposed_dwelling_units);
    $('condoSellout').textContent=d.gross_sellout_low===null?'Needs pricing':d.gross_sellout_low===d.gross_sellout_high?'$'+fmt(d.gross_sellout_low):'$'+fmt(d.gross_sellout_low)+'–$'+fmt(d.gross_sellout_high);
    $('unitTotalsNote').textContent=fmt(d.condo_units_in_sellout)+' condos in sellout ('+fmt(d.market_condo_units)+' market / '+fmt(d.restricted_condo_units)+' restricted) · '+fmt(d.retained_units_outside_sellout)+' units retained outside sellout. '+(d.net_change_from_reported_existing===null?'Net change unresolved because the existing dwelling count is unresolved.':'Net change versus reported existing: '+(d.net_change_from_reported_existing>=0?'+':'')+d.net_change_from_reported_existing+' units.');
    $('condoScenarioStatus').textContent=d.missing_inputs.length?d.missing_inputs.join(' '):d.status+'. Prices and unit counts reflect your entered assumptions.';
    $('condoScenarioLimits').textContent=d.method+' '+d.limitations;
  }catch(e){if(last===report){$('proposedDwellingUnits').textContent='Check inputs';$('condoScenarioStatus').textContent=e.message;}}
  finally{$('calculateCondo').disabled=false;}
}

async function loadCondoReferences(){
  if(!last?.parcel||!last.coordinates)return;
  const report=last,city=report.coverage.municipality;
  const params=new URLSearchParams({municipality:city,lat:report.coordinates.lat,lon:report.coordinates.lon});
  if($('condoMinYear').value.trim())params.set('min_year_built',$('condoMinYear').value.trim());
  if(city.toUpperCase()==='BOSTON'&&/^\d{10}$/.test(report.parcel.parcel_number))params.set('boston_pid',report.parcel.parcel_number);
  $('loadCondoSales').disabled=true;$('applyCondoRange').disabled=true;$('condoCompStatus').textContent='Checking condominium sale records…';
  try{
    const d=await jsonResponse('/api/condo-sales?'+params.toString());if(last!==report)return;
    report.condo_sales_research=d;$('condoReferences').replaceChildren();
    $('condoCompStatus').textContent=d.status+'. '+d.method+' '+d.limitations+(d.truncated?' Source result limit reached; coverage is incomplete.':'')+(d.possible_bundled_records_excluded?' Possible bundled records excluded: '+d.possible_bundled_records_excluded+'.':'');
    for(const c of d.candidates){const item=document.createElement('div');item.className='item';const title=document.createElement('strong');title.textContent=c.address||'Address unavailable';const text=document.createElement('p');text.className='small muted';text.textContent=c.sale_date+' · $'+fmt(c.sale_price)+' · '+fmt(c.living_area_sqft)+' interior sq ft · $'+Number(c.price_per_living_sqft).toFixed(2)+'/sq ft'+(c.year_built_reported?' · Year built (reported): '+c.year_built_reported:'')+(c.record_reference?' · '+c.record_reference:'');const link=document.createElement('a');link.href=c.source_url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Official sale-record source';item.append(title,text,link);$('condoReferences').append(item);}
    $('applyCondoRange').disabled=!(d.observed_price_per_sqft_low>0&&d.observed_price_per_sqft_high>0);
  }catch(e){if(last===report)$('condoCompStatus').textContent=e.message;}
  finally{if(last===report)$('loadCondoSales').disabled=false;}
}

function initializeCondoProgram(){
  $('addCondoMix').onclick=()=>{if($('condoUnitMix').children.length<20)addCondoMix();};
  $('calculateCondo').onclick=calculateCondoProgram;$('loadCondoSales').onclick=loadCondoReferences;
  $('applyCondoRange').onclick=()=>{const refs=last?.condo_sales_research;if(!refs||refs.observed_price_per_sqft_low===null)return;
    for(const row of $('condoUnitMix').children){row.querySelector('[data-field="low_price_per_sqft"]').value=refs.observed_price_per_sqft_low;row.querySelector('[data-field="high_price_per_sqft"]').value=refs.observed_price_per_sqft_high;}
    $('condoScenarioStatus').textContent='Observed condo sale range copied as editable assumptions. Verify new-construction condition and comparability before relying on the sellout scenario.';
  };
}
