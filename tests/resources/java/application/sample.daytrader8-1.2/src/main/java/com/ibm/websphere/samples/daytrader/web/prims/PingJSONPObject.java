/**
 * (C) Copyright IBM Corporation 2015.
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
package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.IOException;
import java.io.StringReader;
import java.io.StringWriter;

import javax.json.Json;
import javax.json.JsonObject;
import javax.json.JsonReader;
import javax.json.stream.JsonGenerator;
import javax.json.stream.JsonParser;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletOutputStream;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *
 * PingJSONP tests JSON generating and parsing 
 *
 */

@WebServlet(name = "PingJSONPObject", urlPatterns = { "/servlet/PingJSONPObject" })
public class PingJSONPObject extends HttpServlet {


  /**
   * 
   */
  private static final long serialVersionUID = -5348806619121122708L;
  private static String initTime;
  private static int hitCount;

  /**
   * forwards post requests to the doGet method Creation date: (11/6/2000
   * 10:52:39 AM)
   *
   * @param res
   *            javax.servlet.http.HttpServletRequest
   * @param res2
   *            javax.servlet.http.HttpServletResponse
   */
  @Override
  public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
    doGet(req, res);
  }

  /**
   * this is the main method of the servlet that will service all get
   * requests.
   *
   * @param request
   *            HttpServletRequest
   * @param responce
   *            HttpServletResponce
   **/
  @Override
  public void doGet(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
    try {
      res.setContentType("text/html");

      ServletOutputStream out = res.getOutputStream();

      hitCount++;

      // JSON generate
      JsonObject json = Json.createObjectBuilder()
          .add("initTime", initTime)
          .add("hitCount", hitCount).build();
      String generatedJSON = json.toString();

      // Read back
      JsonReader jsonReader = Json.createReader(new StringReader(generatedJSON));
      String parsedJSON = jsonReader.readObject().toString();        


      out.println("<html><head><title>Ping JSONP</title></head>"
          + "<body><HR><BR><FONT size=\"+2\" color=\"#000066\">Ping JSONP</FONT><BR>Generated JSON: " + generatedJSON + "<br>Parsed JSON: " + parsedJSON + "</body></html>");
    } catch (Exception e) {
      Log.error(e, "PingJSONPObject.doGet(...): general exception caught");
      res.sendError(500, e.toString());

    }
  }

  /**
   * returns a string of information about the servlet
   *
   * @return info String: contains info about the servlet
   **/
  @Override
  public String getServletInfo() {
    return "Basic JSON generation and parsing in a servlet";
  }

  /**
   * called when the class is loaded to initialize the servlet
   *
   * @param config
   *            ServletConfig:
   **/
  @Override
  public void init(ServletConfig config) throws ServletException {
    super.init(config);
    initTime = new java.util.Date().toString();
    hitCount = 0;
  }
}