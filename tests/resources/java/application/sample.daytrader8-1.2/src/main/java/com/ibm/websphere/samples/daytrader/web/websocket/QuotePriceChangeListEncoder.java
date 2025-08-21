/**
 * (C) Copyright IBM Corporation 2019.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.websocket;

import java.util.Iterator;
import java.util.concurrent.CopyOnWriteArrayList;

import javax.json.Json;
import javax.json.JsonBuilderFactory;
import javax.json.JsonObjectBuilder;
import javax.websocket.EncodeException;
import javax.websocket.Encoder;
import javax.websocket.EndpointConfig;

import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;


/** This class takes a list of quotedata (from the RecentQuotePriceChangeList bean) and encodes 
   it to the json format the client (marektsummary.html) is expecting. **/
public class QuotePriceChangeListEncoder implements Encoder.Text<CopyOnWriteArrayList<QuoteDataBean>> {
  
  private static final JsonBuilderFactory jsonObjectFactory  = Json.createBuilderFactory(null);
  
  public String encode(CopyOnWriteArrayList<QuoteDataBean> list) throws EncodeException {

    JsonObjectBuilder jObjectBuilder = jsonObjectFactory.createObjectBuilder();

    int i = 1;

    for (Iterator<QuoteDataBean> iterator = list.iterator(); iterator.hasNext();) {
      QuoteDataBean quotedata = iterator.next();

      jObjectBuilder.add("change" + i + "_stock", quotedata.getSymbol());
      jObjectBuilder.add("change" + i + "_price","$" + quotedata.getPrice());          
      jObjectBuilder.add("change" + i + "_change", quotedata.getChange());
      i++;
    }

    return jObjectBuilder.build().toString();
  }

  @Override
  public void init(EndpointConfig config) {
    // TODO Auto-generated method stub

  }

  @Override
  public void destroy() {
    // TODO Auto-generated method stub

  }

}
