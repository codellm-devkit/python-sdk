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
package com.ibm.websphere.samples.daytrader.jaxrs;

import java.util.ArrayList;
import java.util.List;

import javax.enterprise.context.RequestScoped;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.inject.Inject;
import javax.ws.rs.Consumes;
import javax.ws.rs.FormParam;
import javax.ws.rs.GET;
import javax.ws.rs.POST;
import javax.ws.rs.Path;
import javax.ws.rs.PathParam;
import javax.ws.rs.Produces;
import javax.ws.rs.core.MediaType;

import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;

@Path("quotes")
@RequestScoped
public class QuoteResource {

  private TradeServices tradeService;  

  
  public QuoteResource() {
  }
  
  @Inject 
  public QuoteResource(@Any Instance<TradeServices> services) {
    tradeService = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }

  @GET
  @Produces(MediaType.APPLICATION_JSON)
  @Path("/{symbols}")
  public List<QuoteDataBean> quotesGet(@PathParam("symbols") String symbols) {
    return getQuotes(symbols);
  }

  @POST
  @Consumes({ "application/x-www-form-urlencoded" })
  @Produces(MediaType.APPLICATION_JSON)
  public List<QuoteDataBean> quotesPost(@FormParam("symbols") String symbols) {
    return getQuotes(symbols);
  }
  
  private List<QuoteDataBean> getQuotes(String symbols) {
    ArrayList<QuoteDataBean> quoteDataBeans = new ArrayList<QuoteDataBean>();

    try {
      String[] symbolsSplit = symbols.split(",");
      for (String symbol: symbolsSplit) {
        QuoteDataBean quoteData = tradeService.getQuote(symbol);
        quoteDataBeans.add(quoteData);
      } 
    } catch (Exception e) {
      e.printStackTrace();
    }

    return (List<QuoteDataBean>)quoteDataBeans;
  }
  
}
